#!/usr/bin/env python3
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]

SOCKET_CPP = ROOT / "TMessagesProj/jni/tgnet/ConnectionSocket.cpp"
SOCKET_H = ROOT / "TMessagesProj/jni/tgnet/ConnectionSocket.h"
ENDPOINT_H = ROOT / "TMessagesProj/jni/tgnet/MtProxyEndpointPolicy.h"
ENDPOINT_CPP = ROOT / "TMessagesProj/jni/tgnet/MtProxyEndpointPolicy.cpp"
ADAPTIVE_H = ROOT / "TMessagesProj/jni/tgnet/MtProxyAdaptivePolicy.h"
ADAPTIVE_CPP = ROOT / "TMessagesProj/jni/tgnet/MtProxyAdaptivePolicy.cpp"
CMAKE = ROOT / "TMessagesProj/jni/CMakeLists.txt"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []
    socket_cpp = read(SOCKET_CPP)
    socket_h = read(SOCKET_H)
    endpoint_h = read(ENDPOINT_H)
    endpoint_cpp = read(ENDPOINT_CPP)
    adaptive_h = read(ADAPTIVE_H)
    adaptive_cpp = read(ADAPTIVE_CPP)
    cmake = read(CMAKE)
    endpoint = endpoint_h + "\n" + endpoint_cpp
    adaptive = adaptive_h + "\n" + adaptive_cpp

    for path, label in (
        (ENDPOINT_H, "MtProxyEndpointPolicy.h"),
        (ENDPOINT_CPP, "MtProxyEndpointPolicy.cpp"),
        (ADAPTIVE_H, "MtProxyAdaptivePolicy.h"),
        (ADAPTIVE_CPP, "MtProxyAdaptivePolicy.cpp"),
    ):
        require(path.exists(), f"{label} must exist", failures)
        require(f"tgnet/{label.replace('.h', '.cpp')}" in cmake or label.endswith(".h"), f"{label} implementation must be compiled by tgnet CMake target", failures)

    for symbol in (
        "class MtProxyEndpointPolicy",
        "struct MtProxyEndpointContext",
        "recordFailure",
        "recordHandshakeOk",
        "recordDataPathSuccess",
        "stateKeyForPhase",
        "useCachedHostAddress",
        "storeResolvedAddress",
        "extractSslipIpv4Address",
        "networkEndpointKeyFor",
        "endpointKeyFor",
        "dnsCacheKeyFor",
        "cooldownMs",
    ):
        require(symbol in endpoint, f"endpoint policy must define {symbol}", failures)
    for symbol in (
        "class MtProxyAdaptivePolicy",
        "applyRecipe",
        "resolveEffectiveTlsProfile",
        "rotateTlsProfileOnFailureIfNeeded",
        "failureNeedsRecipe",
        "adaptiveTlsProfile",
        "tlsAutoRotateProfiles",
    ):
        require(symbol in adaptive, f"adaptive policy must define {symbol}", failures)

    forbidden_socket_symbols = (
        "struct MtProxyEndpointResilienceState",
        "struct MtProxyDnsCacheState",
        "struct MtProxyTlsAutoProfileState",
        "proxyEndpointResilience",
        "proxyEndpointDnsCache",
        "mtProxyTlsAutoRotateProfiles",
        "static bool mtProxyEndpointFailureNeedsRecipe",
        "static int64_t mtProxyEndpointCooldownMs",
        "static int32_t mtProxyEndpointAdaptiveTlsProfile",
        "static bool mtProxyTlsAutoRotateFailureDiagnostic",
        "static void mtProxyRotateTlsProfileOnFailure",
    )
    for symbol in forbidden_socket_symbols:
        require(symbol not in socket_cpp, f"ConnectionSocket.cpp must not own policy symbol {symbol}", failures)

    for call in (
        "MtProxyEndpointPolicy::recordFailure",
        "MtProxyEndpointPolicy::recordHandshakeOk",
        "MtProxyEndpointPolicy::recordDataPathSuccess",
        "MtProxyEndpointPolicy::useCachedHostAddress",
        "MtProxyEndpointPolicy::storeResolvedAddress",
        "MtProxyAdaptivePolicy::applyRecipe",
        "MtProxyAdaptivePolicy::resolveEffectiveTlsProfile",
        "MtProxyAdaptivePolicy::rotateTlsProfileOnFailureIfNeeded",
    ):
        require(call in socket_cpp, f"ConnectionSocket.cpp must delegate through {call}", failures)

    require("#include \"MtProxyEndpointPolicy.h\"" in socket_cpp + socket_h, "ConnectionSocket must include endpoint policy", failures)
    require("#include \"MtProxyAdaptivePolicy.h\"" in socket_cpp + socket_h, "ConnectionSocket must include adaptive policy", failures)

    if failures:
        print("MTProxy policy extraction guard failed:")
        for failure in failures:
            print(f" - {failure}")
        return 1
    print("MTProxy policy extraction guard passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
