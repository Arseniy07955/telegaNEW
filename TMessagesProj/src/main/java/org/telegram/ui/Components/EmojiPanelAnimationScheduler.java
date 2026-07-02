/*
 * This is the source code of Telegram for Android v. 5.x.x.
 * It is licensed under GNU GPL v. 2 or later.
 * You should have received a copy of the license in this archive (see LICENSE).
 */

package org.telegram.ui.Components;

import android.view.View;

import androidx.core.view.ViewCompat;
import androidx.recyclerview.widget.RecyclerView;

import org.telegram.messenger.ImageReceiver;
import org.telegram.ui.Cells.ContextLinkCell;
import org.telegram.ui.Cells.StickerEmojiCell;

import java.util.ArrayList;
import java.util.Collections;

public class EmojiPanelAnimationScheduler {

    public static final int MAX_FULL_RATE_GIFS = 8;
    public static final int MAX_SCROLLING_FULL_RATE_GIFS = 3;
    public static final int MAX_LIMITED_RATE_GIFS = 24;
    public static final int MAX_FULL_RATE_STICKERS = 10;

    private final ArrayList<Candidate> gifCandidates = new ArrayList<>();

    private RecyclerView gifList;
    private RecyclerView stickerList;
    private boolean panelVisible;
    private boolean gifPageVisible;
    private boolean stickerPageVisible;
    private boolean gifScrolling;
    private boolean stickerScrolling;
    private boolean gifInvalidateScheduled;
    private boolean stickerInvalidateScheduled;

    public void setGifList(RecyclerView gifList) {
        this.gifList = gifList;
    }

    public void setStickerList(RecyclerView stickerList) {
        this.stickerList = stickerList;
    }

    public void setPanelVisible(boolean panelVisible) {
        if (this.panelVisible == panelVisible) {
            return;
        }
        this.panelVisible = panelVisible;
        updateGifPlayback();
        updateStickerPlayback();
    }

    public void setGifPageVisible(boolean gifPageVisible) {
        if (this.gifPageVisible == gifPageVisible) {
            return;
        }
        this.gifPageVisible = gifPageVisible;
        updateGifPlayback();
    }

    public void setStickerPageVisible(boolean stickerPageVisible) {
        if (this.stickerPageVisible == stickerPageVisible) {
            return;
        }
        this.stickerPageVisible = stickerPageVisible;
        updateStickerPlayback();
    }

    public void onGifScrollStateChanged(int state) {
        boolean scrolling = state != RecyclerView.SCROLL_STATE_IDLE;
        if (gifScrolling == scrolling) {
            return;
        }
        gifScrolling = scrolling;
        updateGifPlayback();
    }

    public void onStickerScrollStateChanged(int state) {
        boolean scrolling = state != RecyclerView.SCROLL_STATE_IDLE;
        if (stickerScrolling == scrolling) {
            return;
        }
        stickerScrolling = scrolling;
        updateStickerPlayback();
    }

    public void onGifScrolled() {
        updateGifPlayback();
    }

    public void onStickerScrolled() {
        updateStickerPlayback();
    }

    public void onGifCellBound(ContextLinkCell cell) {
        if (cell == null) {
            return;
        }
        cell.setAnimationManagedByScheduler(true);
        ImageReceiver imageReceiver = cell.getPhotoImage();
        imageReceiver.setInvalidateDelegate(this::invalidateGifListOnNextFrame);
        updateGifPlayback();
    }

    public void onGifCellAttached(ContextLinkCell cell) {
        onGifCellBound(cell);
    }

    public void onGifCellDetached(ContextLinkCell cell) {
        if (cell == null) {
            return;
        }
        ImageReceiver imageReceiver = cell.getPhotoImage();
        imageReceiver.setInvalidateDelegate(null);
        imageReceiver.setAnimationLimitFps(false);
        imageReceiver.setAllowDecodeSingleFrame(true);
        imageReceiver.setAllowStartAnimation(false);
        imageReceiver.stopAnimation();
    }

    public void onGifCellRecycled(ContextLinkCell cell) {
        onGifCellDetached(cell);
    }

    public void onStickerCellBound(StickerEmojiCell cell) {
        if (cell == null) {
            return;
        }
        cell.setSchedulerInvalidateDelegate(this::invalidateStickerListOnNextFrame);
        updateStickerPlayback();
    }

    public void onStickerCellAttached(StickerEmojiCell cell) {
        onStickerCellBound(cell);
    }

    public void onStickerCellDetached(StickerEmojiCell cell) {
        if (cell == null) {
            return;
        }
        cell.setSchedulerInvalidateDelegate(null);
        ImageReceiver imageReceiver = cell.getImageView();
        imageReceiver.setAllowStartAnimation(false);
        imageReceiver.setAllowStartLottieAnimation(false);
        imageReceiver.stopAnimation();
    }

    public void onStickerCellRecycled(StickerEmojiCell cell) {
        onStickerCellDetached(cell);
    }

    private void updateGifPlayback() {
        if (gifList == null) {
            return;
        }
        gifCandidates.clear();
        int childCount = gifList.getChildCount();
        for (int i = 0; i < childCount; i++) {
            View child = gifList.getChildAt(i);
            if (!(child instanceof ContextLinkCell)) {
                continue;
            }
            ContextLinkCell cell = (ContextLinkCell) child;
            ImageReceiver imageReceiver = cell.getPhotoImage();
            imageReceiver.setInvalidateDelegate(this::invalidateGifListOnNextFrame);
            int visiblePixels = getVisiblePixels(gifList, child);
            if (panelVisible && gifPageVisible && visiblePixels > 0) {
                gifCandidates.add(new Candidate(cell, visiblePixels, i));
            } else {
                applyGifTier(cell, PlaybackTier.STOPPED);
            }
        }

        Collections.sort(gifCandidates, (left, right) -> {
            int visibleCompare = right.visiblePixels - left.visiblePixels;
            if (visibleCompare != 0) {
                return visibleCompare;
            }
            return left.childIndex - right.childIndex;
        });

        int fullRateBudget = gifScrolling ? MAX_SCROLLING_FULL_RATE_GIFS : MAX_FULL_RATE_GIFS;
        int limitedBudget = Math.max(fullRateBudget, MAX_LIMITED_RATE_GIFS);
        for (int i = 0, count = gifCandidates.size(); i < count; i++) {
            ContextLinkCell cell = gifCandidates.get(i).gifCell;
            if (i < fullRateBudget) {
                applyGifTier(cell, PlaybackTier.FULL_RATE);
            } else if (i < limitedBudget) {
                applyGifTier(cell, PlaybackTier.LIMITED_RATE);
            } else {
                applyGifTier(cell, PlaybackTier.STOPPED);
            }
        }
    }

    private void updateStickerPlayback() {
        if (stickerList == null) {
            return;
        }
        int animatedBudget = stickerScrolling ? Math.max(4, MAX_FULL_RATE_STICKERS / 2) : MAX_FULL_RATE_STICKERS;
        int animatedCount = 0;
        int childCount = stickerList.getChildCount();
        for (int i = 0; i < childCount; i++) {
            View child = stickerList.getChildAt(i);
            if (!(child instanceof StickerEmojiCell)) {
                continue;
            }
            StickerEmojiCell cell = (StickerEmojiCell) child;
            cell.setSchedulerInvalidateDelegate(this::invalidateStickerListOnNextFrame);
            ImageReceiver imageReceiver = cell.getImageView();
            boolean visible = panelVisible && stickerPageVisible && getVisiblePixels(stickerList, child) > 0;
            boolean canRun = visible && (!stickerScrolling || animatedCount++ < animatedBudget);
            if (canRun) {
                imageReceiver.setAllowStartAnimation(true);
                imageReceiver.setAllowStartLottieAnimation(true);
                imageReceiver.startAnimation();
            } else {
                imageReceiver.setAllowStartAnimation(false);
                imageReceiver.setAllowStartLottieAnimation(false);
                imageReceiver.stopAnimation();
            }
        }
    }

    private void applyGifTier(ContextLinkCell cell, PlaybackTier tier) {
        ImageReceiver imageReceiver = cell.getPhotoImage();
        imageReceiver.setInvalidateDelegate(this::invalidateGifListOnNextFrame);
        switch (tier) {
            case FULL_RATE:
                imageReceiver.setAllowDecodeSingleFrame(false);
                imageReceiver.setAnimationLimitFps(false);
                imageReceiver.setAllowStartAnimation(true);
                imageReceiver.startAnimation();
                break;
            case LIMITED_RATE:
                imageReceiver.setAllowDecodeSingleFrame(false);
                imageReceiver.setAnimationLimitFps(true);
                imageReceiver.setAllowStartAnimation(true);
                imageReceiver.startAnimation();
                break;
            case STOPPED:
            default:
                imageReceiver.setAnimationLimitFps(true);
                imageReceiver.setAllowDecodeSingleFrame(true);
                imageReceiver.setAllowStartAnimation(false);
                imageReceiver.stopAnimation();
                break;
        }
    }

    private int getVisiblePixels(RecyclerView parent, View child) {
        int top = Math.max(child.getTop(), parent.getPaddingTop());
        int bottom = Math.min(child.getBottom(), parent.getHeight() - parent.getPaddingBottom());
        return Math.max(0, bottom - top);
    }

    private void invalidateGifListOnNextFrame() {
        if (gifList == null || gifInvalidateScheduled) {
            return;
        }
        gifInvalidateScheduled = true;
        ViewCompat.postOnAnimation(gifList, () -> {
            gifInvalidateScheduled = false;
            ViewCompat.postInvalidateOnAnimation(gifList);
        });
    }

    private void invalidateStickerListOnNextFrame() {
        if (stickerList == null || stickerInvalidateScheduled) {
            return;
        }
        stickerInvalidateScheduled = true;
        ViewCompat.postOnAnimation(stickerList, () -> {
            stickerInvalidateScheduled = false;
            ViewCompat.postInvalidateOnAnimation(stickerList);
        });
    }

    private enum PlaybackTier {
        FULL_RATE,
        LIMITED_RATE,
        STOPPED
    }

    private static class Candidate {
        final ContextLinkCell gifCell;
        final int visiblePixels;
        final int childIndex;

        Candidate(ContextLinkCell gifCell, int visiblePixels, int childIndex) {
            this.gifCell = gifCell;
            this.visiblePixels = visiblePixels;
            this.childIndex = childIndex;
        }
    }
}
