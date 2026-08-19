package com.exteragram.messenger.plugins.ui.components;

import android.content.Context;
import android.view.Gravity;
import android.view.View;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TextView;

import com.exteragram.messenger.plugins.Plugin;

import org.telegram.messenger.AndroidUtilities;
import org.telegram.messenger.R;
import org.telegram.ui.ActionBar.Theme;
import org.telegram.ui.Components.LayoutHelper;
import org.telegram.ui.Components.RecyclerListView;
import org.telegram.ui.Components.UItem;
import org.telegram.ui.Components.UniversalAdapter;
import org.telegram.ui.Components.UniversalRecyclerView;

/**
 * Small compatibility implementation of exteraGram's plugin row.  Its field and
 * method names intentionally match the public/reflection API used by community
 * plugin managers.
 */
public class PluginCell extends FrameLayout {

    private final TextView titleView;
    private final TextView subtitleView;
    private final TextView descriptionView;
    private final ImageView pinButton;
    private final ImageView checkBox;
    private final ImageView deleteButton;
    private final ImageView shareButton;
    private boolean needDivider;
    private Plugin plugin;
    private PluginCellDelegate delegate;

    public PluginCell(Context context) {
        super(context);
        setMinimumHeight(AndroidUtilities.dp(88));
        setPadding(AndroidUtilities.dp(16), AndroidUtilities.dp(10), AndroidUtilities.dp(12), AndroidUtilities.dp(10));

        LinearLayout text = new LinearLayout(context);
        text.setOrientation(LinearLayout.VERTICAL);
        text.setGravity(Gravity.CENTER_VERTICAL);
        addView(text, LayoutHelper.createFrame(LayoutHelper.MATCH_PARENT, LayoutHelper.WRAP_CONTENT,
                Gravity.LEFT | Gravity.CENTER_VERTICAL, 0, 0, 56, 0));

        titleView = makeText(context, 16, Theme.key_windowBackgroundWhiteBlackText);
        titleView.setTypeface(AndroidUtilities.bold());
        text.addView(titleView, LayoutHelper.createLinear(LayoutHelper.MATCH_PARENT, LayoutHelper.WRAP_CONTENT));

        subtitleView = makeText(context, 13, Theme.key_windowBackgroundWhiteGrayText2);
        text.addView(subtitleView, LayoutHelper.createLinear(LayoutHelper.MATCH_PARENT, LayoutHelper.WRAP_CONTENT));

        descriptionView = makeText(context, 13, Theme.key_windowBackgroundWhiteGrayText2);
        descriptionView.setMaxLines(3);
        text.addView(descriptionView, LayoutHelper.createLinear(LayoutHelper.MATCH_PARENT, LayoutHelper.WRAP_CONTENT));

        pinButton = hiddenButton(context, R.drawable.msg_pin);
        checkBox = hiddenButton(context, R.drawable.msg_check_s);
        deleteButton = hiddenButton(context, R.drawable.msg_delete);
        shareButton = createButton(context, R.drawable.msg_share, false, v -> {
            if (delegate != null) delegate.sharePlugin();
        });
        addView(shareButton, LayoutHelper.createFrame(48, 48, Gravity.RIGHT | Gravity.CENTER_VERTICAL));

        setOnClickListener(v -> {
            if (delegate != null) delegate.togglePlugin(v);
        });
        setOnLongClickListener(v -> {
            if (delegate != null) {
                delegate.sharePlugin();
                return true;
            }
            return false;
        });
    }

    private static TextView makeText(Context context, int size, int colorKey) {
        TextView view = new TextView(context);
        view.setTextSize(size);
        view.setTextColor(Theme.getColor(colorKey));
        return view;
    }

    private ImageView hiddenButton(Context context, int icon) {
        ImageView view = createButton(context, icon, false, null);
        view.setVisibility(GONE);
        return view;
    }

    public void set(Plugin plugin, PluginCellDelegate delegate) {
        this.plugin = plugin;
        this.delegate = delegate;
        titleView.setText(plugin != null ? plugin.getName() : "");
        subtitleView.setText(plugin != null ? plugin.getVersion() : "");
        descriptionView.setText(plugin != null ? plugin.getDescription() : "");
        descriptionView.setVisibility(plugin != null && plugin.getDescription() != null ? VISIBLE : GONE);
    }

    public void setNeedDivider(boolean needDivider) {
        this.needDivider = needDivider;
        invalidate();
    }

    public boolean isNeedDivider() {
        return needDivider;
    }

    public ImageView createButton(Context context, int icon, boolean filled, View.OnClickListener listener) {
        ImageView button = new ImageView(context);
        button.setScaleType(ImageView.ScaleType.CENTER);
        button.setImageResource(icon);
        button.setColorFilter(Theme.getColor(Theme.key_windowBackgroundWhiteBlueText));
        button.setBackground(Theme.createSelectorDrawable(0x18000000));
        button.setOnClickListener(listener);
        button.setContentDescription("");
        return button;
    }

    public static class Factory extends UItem.UItemFactory<PluginCell> {
        private static final Factory INSTANCE = new Factory();

        static {
            UItem.UItemFactory.setup(INSTANCE);
        }

        public static UItem asPlugin(Plugin plugin, PluginCellDelegate delegate) {
            UItem item = UItem.ofFactory(Factory.class);
            item.object = plugin;
            item.object2 = delegate;
            item.id = plugin != null && plugin.getId() != null ? plugin.getId().hashCode() : 0;
            return item;
        }

        @Override
        public PluginCell createView(Context context, RecyclerListView listView, int currentAccount,
                                     int classGuid, Theme.ResourcesProvider resourcesProvider) {
            return new PluginCell(context);
        }

        @Override
        public void bindView(View view, UItem item, boolean divider, UniversalAdapter adapter,
                             UniversalRecyclerView listView) {
            ((PluginCell) view).set((Plugin) item.object, (PluginCellDelegate) item.object2);
            ((PluginCell) view).setNeedDivider(divider);
        }
    }
}
