"""Гарантии вокруг потери хода и скачивания черновика.

Прод-инцидент: httpx.ReadError на insert'е рвал сохранение хода целиком, но
клиенту всё равно уезжал artifact со status="ready". Юрист получал кнопку на
документ, которого в базе нет, и вечный 404 «Документ не найден». Отдельно любой
сбой ЧТЕНИЯ выглядел так же — как «документа не существует».

Двойник базы здесь namеренно соблюдает уникальный (session_id, seq) и хранит
вставленные строки: без этого тесты не отличили бы «мы записались, ответ потеряли»
от «диапазон занял другой инстанс», а именно на этом различии держится ретрай.
"""
import httpx
import pytest
from fastapi.testclient import TestClient
from postgrest.exceptions import APIError

from app.server.main import app
from app.server.security import verify_backend_secret
from app.services.supabase_repo import RepoUnavailable, SupabaseRepo


def _api_error(code) -> APIError:
    return APIError({"message": "boom", "code": code, "hint": None, "details": None})


class _Query:
    def __init__(self, db: "FakeDB"):
        self.db = db
        self.kind = None
        self.columns = ""
        self.records: list[dict] = []
        self.filters: dict = {}

    def select(self, columns, *a, **k):
        self.kind = "select"
        self.columns = columns
        return self

    def insert(self, records):
        self.kind = "insert"
        self.records = records
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    async def execute(self):
        return await self.db.run(self)


class _Result:
    def __init__(self, data):
        self.data = data


class FakeDB:
    """Хранит строки и ведёт себя как таблица с unique (session_id, seq)."""

    def __init__(self, *, insert_plan=None, seq_plan=None, read_errors=None):
        self.rows: list[dict] = []
        # Что делать на n-й вставке: "ok" | "lost" (не доехала) |
        # "commit_then_lost" (записалась, но ответ потерян) | исключение.
        self.insert_plan = list(insert_plan or [])
        # Принудительные значения _next_seq (эмулируют устаревшее чтение).
        self.seq_plan = list(seq_plan or [])
        # Сбои чтения: "seq" | "turn"
        self.read_errors = list(read_errors or [])
        self.inserts = 0
        self.seq_reads = 0
        self.turn_checks = 0

    def table(self, name):
        return _Query(self)

    async def run(self, query: _Query):
        if query.kind == "insert":
            return self._insert(query.records)
        if "seq" in query.columns:
            return self._next_seq()
        return self._turn_check(query.filters)

    def _next_seq(self):
        self.seq_reads += 1
        if self.read_errors and self.read_errors[0] == "seq":
            self.read_errors.pop(0)
            raise httpx.ReadError("seq read dropped")
        if self.seq_plan:
            forced = self.seq_plan.pop(0)
            return _Result([{"seq": forced - 1}] if forced > 0 else [])
        seqs = [r["seq"] for r in self.rows]
        return _Result([{"seq": max(seqs)}] if seqs else [])

    def _turn_check(self, filters):
        self.turn_checks += 1
        if self.read_errors and self.read_errors[0] == "turn":
            self.read_errors.pop(0)
            raise httpx.ReadError("turn check dropped")
        found = [
            r
            for r in self.rows
            if r["session_id"] == filters.get("session_id")
            and r.get("turn_id") == filters.get("turn_id")
        ]
        return _Result([{"id": "x"}] if found else [])

    def _commit(self, records):
        taken = {(r["session_id"], r["seq"]) for r in self.rows}
        for record in records:
            key = (record["session_id"], record["seq"])
            if key in taken:
                raise _api_error("23505")  # unique (session_id, seq)
            taken.add(key)
        self.rows.extend(records)

    def _insert(self, records):
        self.inserts += 1
        outcome = self.insert_plan.pop(0) if self.insert_plan else "ok"
        if isinstance(outcome, Exception):
            raise outcome  # отказ ДО коммита: строк в базе не появилось
        if outcome == "lost":
            self._commit(records)
            # откатываем: до базы не доехало
            for record in records:
                self.rows.remove(record)
            raise httpx.ReadError("insert response lost")
        if outcome == "commit_then_lost":
            self._commit(records)
            raise httpx.ReadError("insert response lost")
        self._commit(records)
        return _Result(records)


def _repo(db: FakeDB) -> SupabaseRepo:
    repo = SupabaseRepo.__new__(SupabaseRepo)
    SupabaseRepo.__init__(repo, db)
    return repo


_ROWS = [{"role": "user", "content": "вопрос"}, {"role": "assistant", "content": "ответ"}]


@pytest.mark.asyncio
async def test_transient_failure_is_retried_and_the_turn_survives():
    """Одна сетевая икота не должна стоить юристу хода."""
    db = FakeDB(insert_plan=["lost", "ok"])

    assert await _repo(db).save_messages("s", _ROWS) is True
    assert len(db.rows) == 2
    assert db.inserts == 2


@pytest.mark.asyncio
async def test_commit_whose_response_was_lost_is_not_duplicated():
    """Сбой приходит при чтении ОТВЕТА: вставка могла закоммититься.

    Повтор обязан это заметить по turn_id, иначе переписка задвоится.
    """
    db = FakeDB(insert_plan=["commit_then_lost"])

    assert await _repo(db).save_messages("s", _ROWS) is True
    assert len(db.rows) == 2  # ровно один ход, не два
    assert db.turn_checks == 1


@pytest.mark.asyncio
async def test_seq_taken_by_another_instance_is_recovered_by_re_reading():
    """Гонка за seq между инстансами: лок внутрипроцессный, прод масштабируется.

    Раньше такой ход молча садился в занятый диапазон и перемешивал переписку;
    с уникальным индексом он обязан перечитать seq и записаться следом, а не
    потеряться.
    """
    db = FakeDB(seq_plan=[0])  # устаревшее чтение: диапазон 0..1 уже занят
    db.rows.extend(
        [
            {"session_id": "s", "turn_id": "other", "seq": 0, "role": "user"},
            {"session_id": "s", "turn_id": "other", "seq": 1, "role": "assistant"},
        ]
    )

    assert await _repo(db).save_messages("s", _ROWS) is True
    assert [r["seq"] for r in db.rows if r.get("turn_id") != "other"] == [2, 3]


@pytest.mark.asyncio
async def test_seq_read_failure_loses_the_turn_loudly_instead_of_colliding():
    """Раньше сбой чтения max(seq) молча давал 0 и сажал ход на чужой диапазон."""
    db = FakeDB(read_errors=["seq", "seq", "seq"])
    db.rows.append({"session_id": "s", "turn_id": "other", "seq": 0, "role": "user"})

    assert await _repo(db).save_messages("s", _ROWS) is False
    assert db.inserts == 0
    assert len(db.rows) == 1  # чужой ход не тронут


@pytest.mark.asyncio
async def test_permanent_api_error_is_not_retried():
    """4xx от PostgREST детерминирован: повтор только тратит время."""
    db = FakeDB(insert_plan=[_api_error("22P02")])
    db_repo = _repo(db)

    assert await db_repo.save_messages("s", _ROWS) is False
    assert db.inserts == 1


@pytest.mark.asyncio
async def test_edge_5xx_arrives_as_api_error_and_is_retried():
    """У не-JSON ответа postgrest кладёт в APIError.code сам HTTP-статус."""
    db = FakeDB(insert_plan=[_api_error(503), "ok"])

    assert await _repo(db).save_messages("s", _ROWS) is True
    assert db.inserts == 2


@pytest.mark.asyncio
async def test_exhausted_retries_report_failure():
    db = FakeDB(insert_plan=["lost", "lost", "lost"])

    assert await _repo(db).save_messages("s", _ROWS) is False
    assert db.rows == []


@pytest.mark.asyncio
async def test_failed_turn_check_does_not_claim_success():
    """Не знаем — значит не сохранено: ложный успех оставит юриста с кнопкой
    на несуществующий документ, а это и был исходный баг."""
    db = FakeDB(insert_plan=["lost", "lost", "lost"], read_errors=["turn"])

    assert await _repo(db).save_messages("s", _ROWS) is False


@pytest.mark.asyncio
async def test_read_failure_raises_instead_of_looking_like_missing():
    """Сбой чтения обязан отличаться от «строки нет»."""

    class _Failing:
        def table(self, name):
            return self

        def select(self, *a, **k):
            return self

        def eq(self, *a, **k):
            return self

        def limit(self, *a, **k):
            return self

        async def execute(self):
            raise httpx.ReadError("dropped")

    with pytest.raises(RepoUnavailable):
        await _repo(_Failing()).get_draft_state("s", "call-1")


class _DraftRepo:
    """Репозиторий для эндпоинта рендера: отдаёт состояние или падает."""

    def __init__(self, state=None, fail=False):
        self.state = state
        self.fail = fail

    async def get_draft_state(self, chat_id, draft_id):
        if self.fail:
            raise RepoUnavailable("read failed")
        return self.state


@pytest.fixture
def client():
    app.dependency_overrides[verify_backend_secret] = lambda: None
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_render_returns_503_when_storage_is_unreachable(client):
    """Иначе икота Supabase читается юристом как «вашего документа не существует»."""
    app.state.repo = _DraftRepo(fail=True)

    assert client.get("/chats/s/documents/call-1").status_code == 503


def test_render_returns_404_when_the_turn_was_never_saved(client):
    app.state.repo = _DraftRepo(state=None)

    assert client.get("/chats/s/documents/call-1").status_code == 404


def test_render_returns_404_when_draft_is_not_ready(client):
    app.state.repo = _DraftRepo(state={"status": "failed"})

    assert client.get("/chats/s/documents/call-1").status_code == 404
