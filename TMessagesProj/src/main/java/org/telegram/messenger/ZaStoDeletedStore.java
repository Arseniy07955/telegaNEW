package org.telegram.messenger;

import android.content.Context;
import android.content.SharedPreferences;

import java.util.Collection;
import java.util.Collections;
import java.util.HashSet;
import java.util.Set;

/**
 * Persists "deleted by sender" marks for the anti-delete feature.
 *
 * When the remote side deletes a message we keep it in the DB and only record a mark here so the
 * UI can show it as deleted and the mark survives an app restart. Stored in a per-account
 * SharedPreferences file to avoid touching the messages_v2 schema / DB migrations.
 *
 * Key convention mirrors {@code MessagesController.processUpdateArray} accumulation:
 *   - regular chats (users + basic groups) use the global message-id space -> stored under key 0;
 *   - channels use a per-channel id space -> stored under the channel dialog id ({@code -channel_id}).
 */
public final class ZaStoDeletedStore {

    private static SharedPreferences prefs(int account) {
        return ApplicationLoader.applicationContext.getSharedPreferences("zasto_deleted_" + account, Context.MODE_PRIVATE);
    }

    private static String key(long dialogId) {
        return "d" + dialogId;
    }

    public static synchronized void mark(int account, long dialogId, Collection<Integer> mids) {
        if (mids == null || mids.isEmpty()) {
            return;
        }
        SharedPreferences p = prefs(account);
        Set<String> set = new HashSet<>(p.getStringSet(key(dialogId), Collections.emptySet()));
        boolean changed = false;
        for (Integer m : mids) {
            if (m != null && set.add(String.valueOf((int) m))) {
                changed = true;
            }
        }
        if (changed) {
            p.edit().putStringSet(key(dialogId), set).apply();
        }
    }

    public static Set<Integer> get(int account, long dialogId) {
        Set<String> raw = prefs(account).getStringSet(key(dialogId), null);
        Set<Integer> out = new HashSet<>();
        if (raw != null) {
            for (String s : raw) {
                try {
                    out.add(Integer.parseInt(s));
                } catch (Exception ignore) {
                }
            }
        }
        return out;
    }

    private ZaStoDeletedStore() {
    }
}
