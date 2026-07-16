"""设置对话框：API Key、模型、代理、路径、STT 参数等。"""

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLineEdit,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.config.loader import save_config
from app.config.schema import (
    VALID_COMPUTE_TYPES,
    VALID_MODEL_SIZES,
    VALID_SUMMARY_LANGUAGES,
    AppConfig,
)


class SettingsDialog(QDialog):
    """配置编辑窗口；保存时原子写回 config.yaml。"""

    def __init__(self, config: AppConfig, config_path: Path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setMinimumWidth(480)
        self._config = config
        self._config_path = config_path

        tabs = QTabWidget()
        tabs.addTab(self._llm_tab(), "LLM")
        tabs.addTab(self._network_tab(), "网络")
        tabs.addTab(self._stt_tab(), "语音识别")
        tabs.addTab(self._paths_tab(), "路径")

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addWidget(buttons)

    # ---------------------------------------------------------------- tabs

    def _llm_tab(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        self._base_url = QLineEdit(self._config.llm.base_url)
        self._api_key = QLineEdit(self._config.llm.api_key)
        self._api_key.setEchoMode(QLineEdit.Password)
        self._model = QLineEdit(self._config.llm.model)
        self._timeout = QSpinBox()
        self._timeout.setRange(10, 600)
        self._timeout.setValue(self._config.llm.timeout_seconds)
        self._concurrency = QSpinBox()
        self._concurrency.setRange(1, 16)
        self._concurrency.setValue(self._config.llm.max_concurrency)
        f.addRow("API Base URL", self._base_url)
        f.addRow("API Key", self._api_key)
        f.addRow("模型", self._model)
        f.addRow("超时（秒）", self._timeout)
        f.addRow("并发数", self._concurrency)
        return w

    def _network_tab(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        self._proxy = QLineEdit(self._config.network.proxy)
        self._use_system_proxy = QCheckBox()
        self._use_system_proxy.setChecked(self._config.network.use_system_proxy)
        f.addRow("代理（http://host:port）", self._proxy)
        f.addRow("使用系统代理", self._use_system_proxy)
        return w

    def _stt_tab(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        self._model_size = QComboBox()
        self._model_size.addItems(list(VALID_MODEL_SIZES))
        self._model_size.setCurrentText(self._config.stt.model_size)
        self._compute = QComboBox()
        self._compute.addItems(list(VALID_COMPUTE_TYPES))
        self._compute.setCurrentText(self._config.stt.compute_type)
        self._vad = QCheckBox()
        self._vad.setChecked(self._config.stt.vad_filter)
        self._lang = QComboBox()
        self._lang.addItems(list(VALID_SUMMARY_LANGUAGES))
        self._lang.setCurrentText(self._config.summary.language)
        self._hf_endpoint = QLineEdit(self._config.stt.hf_endpoint)
        f.addRow("Whisper 模型", self._model_size)
        f.addRow("计算精度", self._compute)
        f.addRow("VAD 静音过滤", self._vad)
        f.addRow("总结语言", self._lang)
        f.addRow("HF 镜像", self._hf_endpoint)
        return w

    def _paths_tab(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        self._output_dir = QLineEdit(self._config.paths.output_dir)
        self._cookies = QLineEdit(self._config.downloader.cookies_file)
        grp = QGroupBox("字幕偏好")
        sub_f = QFormLayout(grp)
        sub_f.addRow("输出目录", self._output_dir)
        sub_f.addRow("Cookies 文件", self._cookies)
        f.addRow(grp)
        return w

    def _save(self) -> None:
        from dataclasses import replace

        self._config = replace(
            self._config,
            llm=replace(
                self._config.llm,
                base_url=self._base_url.text(),
                api_key=self._api_key.text(),
                model=self._model.text(),
                timeout_seconds=self._timeout.value(),
                max_concurrency=self._concurrency.value(),
            ),
            network=replace(
                self._config.network,
                proxy=self._proxy.text(),
                use_system_proxy=self._use_system_proxy.isChecked(),
            ),
            stt=replace(
                self._config.stt,
                model_size=self._model_size.currentText(),
                compute_type=self._compute.currentText(),
                vad_filter=self._vad.isChecked(),
                hf_endpoint=self._hf_endpoint.text(),
            ),
            summary=replace(self._config.summary, language=self._lang.currentText()),
            paths=replace(self._config.paths, output_dir=self._output_dir.text()),
            downloader=replace(self._config.downloader, cookies_file=self._cookies.text()),
        )
        save_config(self._config, self._config_path)
        self.accept()

    def get_config(self) -> AppConfig:
        return self._config
