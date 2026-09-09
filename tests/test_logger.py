from __future__ import annotations

import importlib

logger_module = importlib.import_module("wechat_article_reader.shared.utils.logger")


def test_setup_logger_disables_file_queue_by_default(tmp_path, monkeypatch, mocker) -> None:
    monkeypatch.delenv("WECHAT_ARTICLE_READER_LOG_ENQUEUE", raising=False)
    monkeypatch.setattr(logger_module.sys, "stderr", None)
    mocker.patch.object(logger_module.logger, "remove")
    add_mock = mocker.patch.object(logger_module.logger, "add", return_value=1)

    logger_module.setup_logger(log_dir=tmp_path)

    assert add_mock.call_args.kwargs["enqueue"] is False


def test_setup_logger_falls_back_when_file_queue_is_denied(
    tmp_path,
    monkeypatch,
    mocker,
) -> None:
    monkeypatch.setattr(logger_module.sys, "stderr", None)
    mocker.patch.object(logger_module.logger, "remove")
    mocker.patch.object(logger_module.logger, "warning")
    add_mock = mocker.patch.object(
        logger_module.logger,
        "add",
        side_effect=[PermissionError("pipe denied"), 1],
    )

    logger_module.setup_logger(log_dir=tmp_path, file_enqueue=True)

    assert add_mock.call_count == 2
    assert add_mock.call_args_list[0].kwargs["enqueue"] is True
    assert add_mock.call_args_list[1].kwargs["enqueue"] is False
