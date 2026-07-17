"""pytest 配置：将项目根目录加入 sys.path，确保 app 包始终可导入（不依赖 editable install）。"""

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
