/*
 * This is the source code of tgnet library v. 1.1
 * It is licensed under GNU GPL v. 2 or later.
 */

#ifndef MTPROXYADAPTIVEPOLICY_H
#define MTPROXYADAPTIVEPOLICY_H

#include <stdint.h>
#include <string>
#include "MtProxyOptions.h"

class MtProxyAdaptivePolicy {
public:
    enum ClientHelloFamily : int32_t {
        CLIENT_HELLO_CHROME_MODERN_SOFT_FRAGMENT = 0,
        CLIENT_HELLO_CHROME_MODERN_NO_FRAGMENT = 1,
        CLIENT_HELLO_ANDROID_CHROME_NO_FRAGMENT = 2,
        CLIENT_HELLO_FIREFOX_ANDROID_NO_FRAGMENT = 3,
        CLIENT_HELLO_LEGACY_NO_GREASE_NO_MODERN_EXTENSIONS = 4,
        CLIENT_HELLO_LEGACY_TLS12_MINIMAL = 5,
        CLIENT_HELLO_FAMILY_COUNT = 6,
    };

    enum SniVariant : int32_t {
        SNI_ORIGINAL = 0,
        SNI_SANITIZED = 1,
        SNI_LOWERCASE_ASCII = 2,
        SNI_NO_TRAILING_DOT = 3,
        SNI_PUNYCODE = 4,
        SNI_OPTIONAL_NO_SNI = 5,
        SNI_VARIANT_COUNT = 6,
    };

    enum ServerHelloParserVariant : int32_t {
        PARSER_STANDARD_HMAC = 0,
        PARSER_LENIENT_RECORD = 1,
        PARSER_TOLERATE_EXTRA_RECORDS_BEFORE_SERVER_HELLO = 2,
        PARSER_TOLERATE_CCS_TICKET_ORDERING = 3,
        PARSER_TOLERATE_FRAGMENTED_SERVER_HELLO = 4,
        PARSER_TOLERATE_TLS_ALERT_EXACT_DESC = 5,
        PARSER_VARIANT_COUNT = 6,
    };

    enum ClassicVariant : int32_t {
        CLASSIC_NONE = 0,
        CLASSIC_STANDARD_INTERMEDIATE = 1,
        CLASSIC_RANDOMIZED_INTERMEDIATE = 2,
        CLASSIC_ABRIDGED_FALLBACK = 3,
        CLASSIC_INTERMEDIATE_FALLBACK = 4,
        CLASSIC_VARIANT_COUNT = 5,
    };

    struct RecipeCursor {
        int32_t family = CLIENT_HELLO_CHROME_MODERN_SOFT_FRAGMENT;
        int32_t sniVariant = SNI_ORIGINAL;
        int32_t parserVariant = PARSER_STANDARD_HMAC;
        int32_t classicVariant = CLASSIC_NONE;
        uint32_t generation = 0;
    };

    struct CompatibilityRecipe {
        RecipeCursor cursor;
        std::string familyName;
        std::string sniVariantName;
        std::string parserVariantName;
        std::string classicVariantName;
        std::string transportMode;
        std::string tlsProfile;
        std::string clientHelloSni;
        bool fragmentClientHello = false;
        bool useGrease = false;
        bool useModernExtensions = false;
        bool experimentalNoSni = false;
        int32_t clientHelloFragmentation = MT_PROXY_CLIENT_HELLO_FRAGMENTATION_OFF;
        int32_t effectiveTlsProfile = MT_PROXY_TLS_PROFILE_FIREFOX_ANDROID;
        int32_t serverHelloParserMode = MT_PROXY_SERVER_HELLO_PARSER_STANDARD;
        int32_t connectionPatternMode = MT_PROXY_CONNECTION_PATTERN_OFF;
        int32_t recordSizingMode = MT_PROXY_RECORD_SIZING_OFF;
        int32_t timingMode = MT_PROXY_TIMING_OFF;
        int32_t startupCoverMode = MT_PROXY_STARTUP_COVER_OFF;
    };

    struct RecipeInput {
        bool fakeTls = false;
        std::string endpointKey;
        std::string sni;
        std::string originalSni;
        std::string sanitizedSni;
        std::string lowercaseAsciiSni;
        std::string noTrailingDotSni;
        std::string punycodeSni;
        uint32_t allowedSniVariants = 0;
        bool useRecipeCursor = false;
        RecipeCursor cursor;
        bool forceNoGrease = false;
        bool probeGrease = false;
        bool greaseSupported = false;
        std::string lastDiagnostic;
        int32_t recipeLevel = 0;
        int32_t alternateProfileIndex = 0;
        int32_t clientHelloFragmentation = MT_PROXY_CLIENT_HELLO_FRAGMENTATION_OFF;
        int32_t configuredTlsProfile = MT_PROXY_TLS_PROFILE_AUTO;
        int32_t effectiveTlsProfile = MT_PROXY_TLS_PROFILE_FIREFOX_ANDROID;
        int32_t serverHelloParserMode = MT_PROXY_SERVER_HELLO_PARSER_STANDARD;
        int32_t connectionPatternMode = MT_PROXY_CONNECTION_PATTERN_OFF;
        int32_t recordSizingMode = MT_PROXY_RECORD_SIZING_OFF;
        int32_t timingMode = MT_PROXY_TIMING_OFF;
        int32_t startupCoverMode = MT_PROXY_STARTUP_COVER_OFF;
    };

    struct MtProxyRecipe {
        std::string transportMode;
        std::string tlsProfile;
        bool fragmentClientHello = false;
        bool useGrease = false;
        bool useModernExtensions = false;
        std::string serverHelloParser;
        std::string sni;
    };

    struct RecipeResult {
        bool changed = false;
        int32_t recipeLevel = 0;
        int32_t clientHelloFragmentation = MT_PROXY_CLIENT_HELLO_FRAGMENTATION_OFF;
        int32_t effectiveTlsProfile = MT_PROXY_TLS_PROFILE_FIREFOX_ANDROID;
        int32_t serverHelloParserMode = MT_PROXY_SERVER_HELLO_PARSER_STANDARD;
        std::string clientHelloSni;
        int32_t connectionPatternMode = MT_PROXY_CONNECTION_PATTERN_OFF;
        int32_t recordSizingMode = MT_PROXY_RECORD_SIZING_OFF;
        int32_t timingMode = MT_PROXY_TIMING_OFF;
        int32_t startupCoverMode = MT_PROXY_STARTUP_COVER_OFF;
    };

    struct RotateResult {
        bool rotated = false;
        int32_t previousProfile = MT_PROXY_TLS_PROFILE_FIREFOX_ANDROID;
        int32_t nextProfile = MT_PROXY_TLS_PROFILE_FIREFOX_ANDROID;
        uint32_t failures = 0;
    };

    static uint32_t sniVariantMask(int32_t variant);
    static const char *clientHelloFamilyName(int32_t family);
    static const char *sniVariantName(int32_t variant);
    static const char *parserVariantName(int32_t parserVariant);
    static const char *classicVariantName(int32_t classicVariant);
    static RecipeCursor initialCursor(uint32_t allowedSniVariants);
    static bool nextCursor(RecipeCursor *cursor, const std::string &diagnostic, uint32_t allowedSniVariants, bool classicFallbackAllowed);
    static CompatibilityRecipe recipeForCursor(const RecipeInput &input, const RecipeCursor &cursor);
    static RecipeResult applyRecipe(const RecipeInput &input);
    static MtProxyRecipe recipeForResult(const RecipeInput &input, const RecipeResult &result);
    static MtProxyRecipe recipeForCompatibilityRecipe(const CompatibilityRecipe &recipe);
    static std::string recipeId(const CompatibilityRecipe &recipe);
    static std::string recipeId(const MtProxyRecipe &recipe);
    static bool profileUsesGrease(int32_t profile);
    static int32_t resolveEffectiveTlsProfile(int32_t profile, const std::string &key);
    static RotateResult rotateTlsProfileOnFailureIfNeeded(const std::string &key, const std::string &diagnostic, int32_t previousProfile);
    static bool failureNeedsRecipe(const std::string &diagnostic);
    static int32_t compatibilityTlsProfile(int32_t configuredProfile, int32_t effectiveProfile, int32_t recipeLevel);
    static int32_t adaptiveTlsProfile(int32_t configuredProfile, int32_t effectiveProfile);
};

#endif
