package com.exteragram.messenger.plugins.ui.components.templates;

import android.content.Context;
import android.view.View;
import android.widget.FrameLayout;

import org.telegram.messenger.R;
import org.telegram.ui.ActionBar.ActionBar;
import org.telegram.ui.ActionBar.BaseFragment;

/** Generic fragment backed by the delegate shape exposed by exteraGram. */
public class UniversalFragment extends BaseFragment {

    public interface UniversalFragmentDelegate {
        default String getTitle() { return ""; }
        default View beforeCreateView() { return null; }
        default void afterCreateView(View view) { }
        default void onFragmentCreate() { }
        default void onFragmentDestroy() { }
        default Object onBackPressed() { return Boolean.FALSE; }
    }

    private final UniversalFragmentDelegate delegate;

    public UniversalFragment(UniversalFragmentDelegate delegate) {
        this.delegate = delegate;
    }

    @Override
    public boolean onFragmentCreate() {
        boolean result = super.onFragmentCreate();
        if (delegate != null) delegate.onFragmentCreate();
        return result;
    }

    @Override
    public View createView(Context context) {
        actionBar.setBackButtonImage(R.drawable.ic_ab_back);
        actionBar.setAllowOverlayTitle(true);
        actionBar.setTitle(delegate != null ? delegate.getTitle() : "");
        actionBar.setActionBarMenuOnItemClick(new ActionBar.ActionBarMenuOnItemClick() {
            @Override
            public void onItemClick(int id) {
                if (id == -1) finishFragment();
            }
        });
        fragmentView = delegate != null ? delegate.beforeCreateView() : null;
        if (fragmentView == null) fragmentView = new FrameLayout(context);
        if (delegate != null) delegate.afterCreateView(fragmentView);
        return fragmentView;
    }

    @Override
    public void onFragmentDestroy() {
        if (delegate != null) delegate.onFragmentDestroy();
        super.onFragmentDestroy();
    }

    @Override
    public boolean onBackPressed(boolean invoked) {
        if (delegate != null) {
            Object handled = delegate.onBackPressed();
            if (handled instanceof Boolean && (Boolean) handled) return false;
        }
        return super.onBackPressed(invoked);
    }

    public void setTitle(CharSequence title, boolean animated, int id) {
        if (actionBar != null) actionBar.setTitle(title);
    }
}
