#!/usr/bin/env python3
"""Рендерит docker-compose для COI VM из шаблона + переменных окружения CI.

Почему не envsubst: значения секретов должны попадать в YAML безопасно
(кавычки, спецсимволы), а каждый '$' в значении обязан превратиться в '$$' —
иначе docker compose на VM попытается интерполировать его из пустого окружения
и молча подставит пустую строку.

Токены шаблона:
  __KV:NAME__  -> - "NAME=<значение из env>". Строка удаляется целиком, если
                  переменная пуста или не задана — паритет с serverless-деплоем,
                  где опциональные переменные просто не попадали в --environment.
  __NAME__     -> прямая подстановка значения (образы, домен). Пустое значение —
                  ошибка: такие токены обязательны.

После рендера в файле не должно остаться ни одного токена и ни одного '${' —
иначе exit 1. Никаких тихих деградаций.

Использование: render_compose.py <шаблон> <выходной файл>
"""
import json
import os
import re
import sys

KV_TOKEN = re.compile(r"__KV:([A-Z0-9_]+)__")
DIRECT_TOKEN = re.compile(r"__([A-Z0-9_]+)__")


def escape(value: str) -> str:
    # docker compose интерполирует $VAR/${VAR} в значениях; '$$' — литеральный '$'
    return value.replace("$", "$$")


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit("usage: render_compose.py <template> <output>")
    tpl_path, out_path = sys.argv[1], sys.argv[2]

    template = open(tpl_path, encoding="utf-8").read()

    # Опечатки в токенах (регистр, ':' не там) не матчатся основными регекспами
    # и молча уехали бы в compose литералом — ловим их на стороне ШАБЛОНА.
    for candidate in re.findall(r"__[A-Za-z0-9:_]+?__", template):
        if not (KV_TOKEN.fullmatch(candidate) or DIRECT_TOKEN.fullmatch(candidate)):
            sys.exit(f"render_compose: malformed token in template: {candidate}")

    rendered_lines = []
    dropped = []
    for line in template.splitlines():
        kv = KV_TOKEN.search(line)
        if kv:
            name = kv.group(1)
            value = os.environ.get(name, "")
            if value == "":
                dropped.append(name)
                continue
            quoted = json.dumps(f"{name}={escape(value)}", ensure_ascii=False)
            rendered_lines.append(line[: kv.start()] + quoted + line[kv.end():])
            continue

        def substitute(match: re.Match) -> str:
            name = match.group(1)
            value = os.environ.get(name, "")
            if value == "":
                sys.exit(f"render_compose: required var {name} is empty")
            return escape(value)

        rendered_lines.append(DIRECT_TOKEN.sub(substitute, line))

    output = "\n".join(rendered_lines) + "\n"

    # Каждый токен шаблона к этому моменту либо подставлен, либо (KV с пустым
    # значением) выброшен — сам output токен-скан не проходит: значения секретов
    # могут случайно содержать похожие на токены подстроки (base64url это
    # допускает), и скан по output давал бы ложные срабатывания.
    #
    # '$'-гард: каждый '$' из значений экранирован в '$$'; схлопнув пары, ловим
    # только НЕэкранированные подстановки, случайно оставшиеся в шаблоне.
    if "${" in output.replace("$$", ""):
        sys.exit("render_compose: template contains an unescaped '${' — compose on the VM would interpolate it")

    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(output)

    # Имена (не значения!) выброшенных опциональных переменных — в лог CI,
    # чтобы отсутствие LangSmith/прокси было видно, а не терялось молча.
    if dropped:
        print(f"render_compose: dropped empty optional vars: {', '.join(dropped)}")
    print(f"render_compose: wrote {out_path} ({len(output)} bytes)")


if __name__ == "__main__":
    main()
