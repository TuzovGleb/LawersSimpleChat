"""Нормативка: ingestion of statutes from pravo.gov.ru ИПС «Законодательство России».

The package is the only place that knows the ИПС wire format (windows-1251,
frame layout, the full GET field map, RTF export). Downstream code deals with
plain dataclasses: catalog entries, act metadata and parsed articles.
"""
from app.normativka.acts import CODICES, KnownAct, resolve_act
from app.normativka.catalog import CatalogEntry, enumerate_acts
from app.normativka.fetch import ActMeta, fetch_act_meta, fetch_act_text
from app.normativka.ips_client import IpsClient, IpsError
from app.normativka.parse import Article, split_articles

__all__ = [
    "CODICES",
    "KnownAct",
    "resolve_act",
    "CatalogEntry",
    "enumerate_acts",
    "ActMeta",
    "fetch_act_meta",
    "fetch_act_text",
    "IpsClient",
    "IpsError",
    "Article",
    "split_articles",
]
