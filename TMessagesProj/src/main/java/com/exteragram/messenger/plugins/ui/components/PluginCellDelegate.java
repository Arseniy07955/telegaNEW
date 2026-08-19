package com.exteragram.messenger.plugins.ui.components;

import android.view.View;

/** exteraGram-compatible callbacks used by plugin-list cells. */
public interface PluginCellDelegate {
    default boolean canOpenInExternalApp() { return false; }
    default void deletePlugin() { }
    default void openInExternalApp() { }
    default void openPluginSettings() { }
    default void pinPlugin(View view) { }
    default void sharePlugin() { }
    default void togglePlugin(View view) { }
}
