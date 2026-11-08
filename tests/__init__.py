"""测试包：把 scripts/ 加入导入路径，测试直接导入各模块。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
