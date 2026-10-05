package org.telegram.ui.Cells;

import static org.telegram.messenger.AndroidUtilities.dp;
import static org.telegram.messenger.AndroidUtilities.dpf2;

import android.content.Context;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.Space;
import android.widget.TextView;

import org.telegram.messenger.AndroidUtilities;
import org.telegram.messenger.LocaleController;
import org.telegram.messenger.R;
import org.telegram.ui.ActionBar.Theme;
import org.telegram.ui.Components.LayoutHelper;

/**
 * Одноразовая плашка над списком чатов: предлагает заглянуть в канал клиента.
 * Устроена как {@link UnconfirmedAuthHintCell}, чтобы выглядеть в списке своей.
 */
public class ChannelPromoHintCell extends FrameLayout {

    private final LinearLayout linearLayout;
    private final TextView titleTextView;
    private final TextView messageTextView;
    private final TextView openButton;
    private final TextView dismissButton;

    private int height;

    public ChannelPromoHintCell(Context context) {
        super(context);

        setClickable(true);

        linearLayout = new LinearLayout(context);
        linearLayout.setOrientation(LinearLayout.VERTICAL);

        titleTextView = new TextView(context);
        titleTextView.setGravity(Gravity.CENTER);
        titleTextView.setTextSize(TypedValue.COMPLEX_UNIT_DIP, 14);
        titleTextView.setTypeface(AndroidUtilities.bold());
        titleTextView.setText(LocaleController.getString(R.string.ChannelPromoTitle));
        linearLayout.addView(titleTextView, LayoutHelper.createLinear(LayoutHelper.MATCH_PARENT, LayoutHelper.WRAP_CONTENT, 0, Gravity.TOP | Gravity.FILL_HORIZONTAL, 28, 8, 28, 0));

        messageTextView = new TextView(context);
        messageTextView.setGravity(Gravity.CENTER);
        messageTextView.setTextSize(TypedValue.COMPLEX_UNIT_DIP, 13);
        messageTextView.setLineSpacing(dpf2(2), 1);
        messageTextView.setText(LocaleController.getString(R.string.ChannelPromoMessage));
        linearLayout.addView(messageTextView, LayoutHelper.createLinear(LayoutHelper.MATCH_PARENT, LayoutHelper.WRAP_CONTENT, 0, Gravity.TOP | Gravity.FILL_HORIZONTAL, 28, 2, 28, 0));

        LinearLayout buttonsLayout = new LinearLayout(context);
        buttonsLayout.setOrientation(LinearLayout.HORIZONTAL);
        buttonsLayout.setGravity(Gravity.CENTER);

        buttonsLayout.addView(new Space(context), LayoutHelper.createLinear(LayoutHelper.WRAP_CONTENT, 1, Gravity.CENTER, 1));

        openButton = makeButton(context, R.string.ChannelPromoOpen);
        buttonsLayout.addView(openButton, LayoutHelper.createLinear(LayoutHelper.WRAP_CONTENT, 30));

        buttonsLayout.addView(new Space(context), LayoutHelper.createLinear(LayoutHelper.WRAP_CONTENT, 1, Gravity.CENTER, 1));

        dismissButton = makeButton(context, R.string.ChannelPromoDismiss);
        buttonsLayout.addView(dismissButton, LayoutHelper.createLinear(LayoutHelper.WRAP_CONTENT, 30));

        buttonsLayout.addView(new Space(context), LayoutHelper.createLinear(LayoutHelper.WRAP_CONTENT, 1, Gravity.CENTER, 1));

        linearLayout.addView(buttonsLayout, LayoutHelper.createLinear(LayoutHelper.MATCH_PARENT, LayoutHelper.WRAP_CONTENT, 28, 4, 28, 8));

        addView(linearLayout, LayoutHelper.createFrame(LayoutHelper.MATCH_PARENT, LayoutHelper.MATCH_PARENT, Gravity.FILL));

        updateColors();
    }

    private static TextView makeButton(Context context, int textRes) {
        TextView button = new TextView(context);
        button.setPadding(dp(10), dp(5), dp(10), dp(7));
        button.setTypeface(AndroidUtilities.bold());
        button.setTextSize(TypedValue.COMPLEX_UNIT_DIP, 14.22f);
        button.setText(LocaleController.getString(textRes));
        button.setGravity(Gravity.CENTER);
        return button;
    }

    public void set(View.OnClickListener onOpen, View.OnClickListener onDismiss) {
        openButton.setOnClickListener(onOpen);
        dismissButton.setOnClickListener(onDismiss);
    }

    public void updateColors() {
        int accent = Theme.getColor(Theme.key_windowBackgroundWhiteValueText);
        int gray = Theme.getColor(Theme.key_windowBackgroundWhiteGrayText);
        float alpha = Theme.isCurrentThemeDark() ? .3f : .15f;
        titleTextView.setTextColor(Theme.getColor(Theme.key_windowBackgroundWhiteBlackText));
        messageTextView.setTextColor(gray);
        openButton.setTextColor(accent);
        openButton.setBackground(Theme.createSelectorDrawable(Theme.multAlpha(accent, alpha), Theme.RIPPLE_MASK_ROUNDRECT_6DP, dp(8)));
        dismissButton.setTextColor(gray);
        dismissButton.setBackground(Theme.createSelectorDrawable(Theme.multAlpha(gray, alpha), Theme.RIPPLE_MASK_ROUNDRECT_6DP, dp(8)));
    }

    @Override
    protected void onMeasure(int widthMeasureSpec, int heightMeasureSpec) {
        int width = MeasureSpec.getSize(widthMeasureSpec);
        if (width <= 0) {
            width = AndroidUtilities.displaySize.x;
        }
        linearLayout.measure(
            MeasureSpec.makeMeasureSpec(width - getPaddingLeft() - getPaddingRight(), MeasureSpec.EXACTLY),
            MeasureSpec.makeMeasureSpec(AndroidUtilities.displaySize.y, MeasureSpec.AT_MOST)
        );
        height = linearLayout.getMeasuredHeight() + getPaddingTop() + getPaddingBottom() + 1;
        super.onMeasure(widthMeasureSpec, MeasureSpec.makeMeasureSpec(height, MeasureSpec.EXACTLY));
    }

    public int height() {
        if (getVisibility() != View.VISIBLE) {
            return 0;
        }
        if (height <= 0) {
            height = dp(72) + 1;
        }
        return height;
    }
}
