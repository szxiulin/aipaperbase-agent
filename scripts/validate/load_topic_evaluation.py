from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.analytics.evaluation import (  # noqa: E402
    DEFAULT_REVIEWS,
    DEFAULT_SAMPLE,
    load_topic_evaluation,
)
from backend.catalog.database import DEFAULT_DATABASE, connect  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="将可追溯主题评估样本与审阅结果加载到本地数据库")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--sample", type=Path, default=DEFAULT_SAMPLE)
    parser.add_argument("--reviews", type=Path, default=DEFAULT_REVIEWS)
    args = parser.parse_args()
    with connect(args.database) as connection:
        result = load_topic_evaluation(connection, args.sample, args.reviews)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
