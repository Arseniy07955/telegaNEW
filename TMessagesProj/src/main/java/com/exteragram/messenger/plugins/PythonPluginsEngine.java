package com.exteragram.messenger.plugins;

/** exteraGram engine facade used by plugin stores after writing a .py file. */
public class PythonPluginsEngine {

    public void loadPlugins(Object ignored) {
        org.telegram.plugins.PluginsController.getInstance().reloadPluginsFromDisk();
    }
}
