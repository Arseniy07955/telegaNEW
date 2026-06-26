package org.telegram.messenger;

/**
 * ZaStoGram privacy/retention overrides.
 *
 * Central, hardcoded switches that make the client stop enforcing sender-imposed
 * ephemerality on content already delivered to this device. All flags are always-on
 * by design (see plan: toasty-cooking-wozniak.md); kept in one place so the diff is
 * easy to audit and trivial to revert by flipping a single boolean.
 */
public final class ZaStoPrivacy {

    /** Keep messages that the remote side deletes (anti-delete), marked instead of removed. */
    public static final boolean KEEP_DELETED = true;

    /** Keep self-destruct / TTL / view-once media; never run the local destruction. */
    public static final boolean KEEP_EPHEMERAL = true;

    /** Do not apply FLAG_SECURE on viewers of other people's content (allow screenshots). */
    public static final boolean ALLOW_SCREENSHOTS = true;

    /** Allow saving/forwarding of content-protected (noforwards) media and stories. */
    public static final boolean ALLOW_SAVE_PROTECTED = true;

    /** Do not send the "took a screenshot" service message to the other party. */
    public static final boolean MUTE_SCREENSHOT_PING = true;

    private ZaStoPrivacy() {
    }
}
