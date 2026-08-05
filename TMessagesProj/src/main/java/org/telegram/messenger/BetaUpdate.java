package org.telegram.messenger;

import androidx.annotation.Nullable;

public class BetaUpdate {

    public final String version;
    public final int versionCode;
    public final long updateOrder;

    @Nullable
    public final String changelog;

    public BetaUpdate(String version, int versionCode, String changelog) {
        this(version, versionCode, changelog, 0L);
    }

    public BetaUpdate(String version, int versionCode, String changelog, long updateOrder) {
        this.version = version;
        this.versionCode = versionCode;
        this.changelog = changelog;
        this.updateOrder = updateOrder;
    }

    public boolean higherThan(BetaUpdate update) {
        if (update == null) {
            return true;
        }
        if (updateOrder != 0 && update.updateOrder != 0) {
            return updateOrder > update.updateOrder;
        }
        return SharedConfig.versionBiggerOrEqual(version, update.version) && versionCode > update.versionCode;
    }

}
