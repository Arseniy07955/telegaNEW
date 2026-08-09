package org.telegram.ui.Components;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.ColorFilter;
import android.graphics.Paint;
import android.graphics.PixelFormat;
import android.graphics.Rect;
import android.graphics.RectF;
import android.graphics.drawable.Drawable;
import android.os.SystemClock;

import androidx.annotation.NonNull;

import org.telegram.messenger.AndroidUtilities;
import org.telegram.messenger.R;

public class ProxyDrawable extends Drawable {

    private final Drawable emptyDrawable;
    private final Drawable fullDrawable;

    private final Paint outerPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint alertPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint alertBorderPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final RectF circleRect = new RectF();
    private int radOffset = 0;
    private long lastUpdateTime;

    private float connectedAnimationProgress;

    private boolean connected;
    private boolean isEnabled;
    private int alertLevel;
    private int alertColor = 0xffff9800;

    public ProxyDrawable(Context context) {
        super();
        emptyDrawable = context.getResources().getDrawable(R.drawable.outline_shield_plain_24).mutate();
        fullDrawable = context.getResources().getDrawable(R.drawable.outline_shield_check).mutate();

        outerPaint.setColor(Color.WHITE);
        outerPaint.setStyle(Paint.Style.STROKE);
        outerPaint.setStrokeWidth(AndroidUtilities.dp(1.66f));
        outerPaint.setStrokeCap(Paint.Cap.ROUND);
        alertBorderPaint.setStyle(Paint.Style.STROKE);
        alertBorderPaint.setStrokeWidth(AndroidUtilities.dp(1.5f));
        alertBorderPaint.setColor(0xffffffff);
        lastUpdateTime = SystemClock.elapsedRealtime();
    }

    public void setConnected(boolean enabled, boolean value, boolean animated) {
        setStatus(enabled, value, 0, alertColor, animated);
    }

    public void setStatus(boolean enabled, boolean value, int alertLevel, int alertColor, boolean animated) {
        isEnabled = enabled;
        connected = value;
        this.alertLevel = Math.max(0, alertLevel);
        this.alertColor = alertColor;
        lastUpdateTime = SystemClock.elapsedRealtime();
        if (!animated) {
            connectedAnimationProgress = connected ? 1.0f : 0.0f;
        }
        invalidateSelf();
    }

    @Override
    public void draw(@NonNull Canvas canvas) {
        long newTime = SystemClock.elapsedRealtime();
        long dt = newTime - lastUpdateTime;
        lastUpdateTime = newTime;

        if (!isEnabled) {
            setBounds(emptyDrawable);
            emptyDrawable.draw(canvas);
        } else if (!connected || connectedAnimationProgress != 1.0f) {
            setBounds(emptyDrawable);
            emptyDrawable.draw(canvas);
            outerPaint.setAlpha((int) (255 * (1.0f - connectedAnimationProgress)));

            radOffset += (int) (360 * dt / 1000.0f);

            int width = getBounds().width();
            int height = getBounds().height();

            int r = AndroidUtilities.dp(4);
            int x = width / 2 - r;
            int y = height / 2 - r;
            circleRect.set(x, y, x + r + r, y + r + r);
            canvas.drawArc(circleRect, -90 + radOffset, 90, false, outerPaint);
            invalidateSelf();
        }

        if (isEnabled && (connected || connectedAnimationProgress != 0.0f)) {
            fullDrawable.setAlpha((int) (255 * connectedAnimationProgress));
            setBounds(fullDrawable);
            fullDrawable.draw(canvas);
        }

        if (connected && connectedAnimationProgress != 1.0f) {
            connectedAnimationProgress += dt / 300.0f;
            if (connectedAnimationProgress > 1.0f) {
                connectedAnimationProgress = 1.0f;
            }
            invalidateSelf();
        } else if (!connected && connectedAnimationProgress != 0.0f) {
            connectedAnimationProgress -= dt / 300.0f;
            if (connectedAnimationProgress < 0.0f) {
                connectedAnimationProgress = 0.0f;
            }
            invalidateSelf();
        }

        if (isEnabled && alertLevel > 0) {
            Rect bounds = getBounds();
            float radius = AndroidUtilities.dp(alertLevel >= 2 ? 4.0f : 3.5f);
            float cx = bounds.centerX() + AndroidUtilities.dp(7);
            float cy = bounds.centerY() + AndroidUtilities.dp(7);
            alertPaint.setColor(alertColor);
            canvas.drawCircle(cx, cy, radius + AndroidUtilities.dp(1), alertBorderPaint);
            canvas.drawCircle(cx, cy, radius, alertPaint);
        }
    }

    private void setBounds(Drawable drawable) {
        Rect bounds = getBounds();
        drawable.setBounds(
            bounds.centerX() - drawable.getIntrinsicWidth() / 2,
            bounds.centerY() - drawable.getIntrinsicHeight() / 2,
            bounds.centerX() + drawable.getIntrinsicWidth() / 2,
            bounds.centerY() + drawable.getIntrinsicHeight() / 2
        );
    }

    @Override
    public void setAlpha(int alpha) {

    }

    @Override
    public void setColorFilter(ColorFilter cf) {
        emptyDrawable.setColorFilter(cf);
        fullDrawable.setColorFilter(cf);
        outerPaint.setColorFilter(cf);
    }

    @Override
    public int getOpacity() {
        return PixelFormat.TRANSPARENT;
    }

    @Override
    public int getIntrinsicWidth() {
        return AndroidUtilities.dp(24);
    }

    @Override
    public int getIntrinsicHeight() {
        return AndroidUtilities.dp(24);
    }
}
