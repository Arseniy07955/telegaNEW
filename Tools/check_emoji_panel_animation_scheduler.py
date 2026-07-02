#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

EMOJI_VIEW = ROOT / "TMessagesProj/src/main/java/org/telegram/ui/Components/EmojiView.java"
SCHEDULER = ROOT / "TMessagesProj/src/main/java/org/telegram/ui/Components/EmojiPanelAnimationScheduler.java"
IMAGE_RECEIVER = ROOT / "TMessagesProj/src/main/java/org/telegram/messenger/ImageReceiver.java"
CONTEXT_LINK_CELL = ROOT / "TMessagesProj/src/main/java/org/telegram/ui/Cells/ContextLinkCell.java"
STICKER_EMOJI_CELL = ROOT / "TMessagesProj/src/main/java/org/telegram/ui/Cells/StickerEmojiCell.java"
PLAN = ROOT / "docs/superpowers/plans/2026-07-02-emoji-panel-animation-scheduler.md"
SPEC = ROOT / "docs/superpowers/specs/2026-07-02-emoji-panel-animation-scheduler-design.md"


def read(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []

    emoji_view = read(EMOJI_VIEW)
    scheduler = read(SCHEDULER)
    image_receiver = read(IMAGE_RECEIVER)
    context_link = read(CONTEXT_LINK_CELL)
    sticker_cell = read(STICKER_EMOJI_CELL)
    plan = read(PLAN)
    spec = read(SPEC)

    require("class EmojiPanelAnimationScheduler" in scheduler, "scheduler class must exist", failures)
    for token in (
        "MAX_FULL_RATE_GIFS",
        "MAX_SCROLLING_FULL_RATE_GIFS",
        "MAX_FULL_RATE_STICKERS",
        "setPanelVisible(boolean",
        "setGifPageVisible(boolean",
        "setStickerPageVisible(boolean",
        "onGifCellBound(ContextLinkCell",
        "onStickerCellBound(StickerEmojiCell",
        "setAnimationLimitFps(",
        "setInvalidateDelegate(",
        "postInvalidateOnAnimation",
    ):
        require(token in scheduler, f"scheduler must contain {token}", failures)

    require("setAnimationLimitFps(boolean" in image_receiver, "ImageReceiver must expose animation FPS limit hook", failures)
    require("animationLimitFps" in image_receiver, "ImageReceiver must retain animation FPS limit for future drawables", failures)
    require("setInvalidateDelegate(Runnable" in image_receiver, "ImageReceiver must expose invalidate delegate hook", failures)
    require("invalidateDelegate.run()" in image_receiver, "ImageReceiver.invalidate must use delegate when present", failures)
    require("fileDrawable.setLimitFps(animationLimitFps)" in image_receiver, "ImageReceiver must apply FPS limit to AnimatedFileDrawable", failures)

    require("setAnimationManagedByScheduler(boolean" in context_link, "ContextLinkCell must allow scheduler-managed GIF playback", failures)
    require("animationManagedByScheduler" in context_link, "ContextLinkCell must store scheduler-managed playback state", failures)
    require("!animationManagedByScheduler" in context_link, "ContextLinkCell must not auto-start managed keyboard GIFs", failures)

    require("setSchedulerInvalidateDelegate(Runnable" in sticker_cell, "StickerEmojiCell must expose scheduler invalidation delegate", failures)
    require("setInvalidateAll(false)" in sticker_cell, "StickerEmojiCell must stop per-frame full-parent invalidation", failures)
    require("schedulerInvalidateDelegate" in sticker_cell, "StickerEmojiCell must retain scheduler invalidation delegate", failures)

    for token in (
        "emojiPanelAnimationScheduler",
        "new EmojiPanelAnimationScheduler()",
        "emojiPanelAnimationScheduler.setGifList(gifGridView)",
        "emojiPanelAnimationScheduler.setStickerList(stickersGridView)",
        "emojiPanelAnimationScheduler.onGifCellBound(cell)",
        "emojiPanelAnimationScheduler.onStickerCellBound(cell)",
        "emojiPanelAnimationScheduler.setGifPageVisible(start)",
        "ensureGifTrendingLoaded()",
        "suppressSearchTextChanged",
    ):
        require(token in emoji_view, f"EmojiView must wire {token}", failures)

    ensure_start = emoji_view.find("private void ensureGifTrendingLoaded()")
    ensure_body = emoji_view[ensure_start:ensure_start + 500] if ensure_start >= 0 else ""
    construction_start = emoji_view.find("gifTabs.setDelegate(page ->")
    construction_end = emoji_view.find("stickersContainer = new FrameLayout", construction_start)
    construction_body = emoji_view[construction_start:construction_end] if construction_start >= 0 and construction_end >= 0 else ""
    require("gifAdapter.loadTrendingGifs();" in ensure_body, "ensureGifTrendingLoaded must own the main GIF trending request", failures)
    require("gifAdapter.loadTrendingGifs();" not in construction_body, "GIF trending must not eagerly load in constructor", failures)
    require("if (suppressSearchTextChanged)" in emoji_view, "Search TextWatcher must suppress programmatic tab-sync searches", failures)

    require("EmojiPanelAnimationScheduler" in plan, "implementation plan must mention the scheduler", failures)
    require("EmojiPanelAnimationScheduler" in spec, "design spec must mention the scheduler", failures)

    if failures:
        print("Emoji panel animation scheduler guard failed:", file=__import__("sys").stderr)
        for failure in failures:
            print(f" - {failure}", file=__import__("sys").stderr)
        return 1
    print("Emoji panel animation scheduler guard passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
