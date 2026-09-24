package org.telegram.ui.Components;
public class ForegroundDetector {
    public interface Listener { void onBecameForeground(); void onBecameBackground(); }
    public static ForegroundDetector getInstance() { return null; }
    public boolean isBackground() { return false; }
    public void addListener(Listener l) {}
    public void removeListener(Listener l) {}
}
