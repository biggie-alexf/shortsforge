"""Генерация синтетических фикстур: `cd backend && python -m scripts.make_fixtures`.

Создаёт в {DATA_DIR}/_fixtures: letsplay_1..3.mp4 (1920x1080, 60 с),
music.wav (30 с эмбиент-дрон), sfx/{impact,tick,sting,glitch}.wav.
"""
from __future__ import annotations

import argparse
import logging


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Generate ShortForge mock fixtures")
    parser.add_argument(
        "--force", action="store_true", help="перегенерировать существующие файлы"
    )
    args = parser.parse_args()

    from shortforge.media.fixtures import ensure_fixtures, fixtures_dir

    paths = ensure_fixtures(force=args.force)
    print(f"fixtures dir: {fixtures_dir()}")
    for p in paths:
        print(f"  {p.name}: {p.stat().st_size} bytes")


if __name__ == "__main__":
    main()
