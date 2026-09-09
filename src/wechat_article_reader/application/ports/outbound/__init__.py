"""出站端口 - 定义应用层依赖的外部服务接口"""

from .exporter_port import AsyncExporterPort, ExporterPort
from .repository import ArticleRepository, UnitOfWork
from .scraper_port import AsyncScraperPort, ScraperPort
from .storage_port import StoragePort
from .summarizer_port import SummarizerPort

__all__ = [
    "ArticleRepository",
    "AsyncExporterPort",
    "AsyncScraperPort",
    "ExporterPort",
    "ScraperPort",
    "StoragePort",
    "SummarizerPort",
    "UnitOfWork",
]
