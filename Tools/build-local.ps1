<#
  Локальная сборка telegaNEW под одну архитектуру (по умолчанию arm64-v8a).

  Повторяет команду из .github/workflows/build-apk.yml, но без публикации:
  APK кладётся в dist-local\ и по желанию ставится на телефон по adb.

  Секреты лежат в local-build.env рядом с репозиторием (файл в .gitignore):
    TELEGRAM_API_ID=...
    TELEGRAM_API_HASH=...
    KEYSTORE_PATH=C:\путь\к\release.keystore   # ключ ВНЕ репозитория
    KEYSTORE_PASSWORD=...
    KEY_ALIAS=...                               # необязательно, по умолчанию zastogram
    KEY_PASSWORD=...                            # необязательно, по умолчанию как пароль хранилища
  Без KEYSTORE_PATH APK подпишется публичным ключом из репозитория: такая сборка
  не обновит приложение, поставленное из GitHub (другая подпись).

  Примеры:
    powershell -File Tools\build-local.ps1
    powershell -File Tools\build-local.ps1 -Install
    powershell -File Tools\build-local.ps1 -Abi armeabi-v7a -BuildNumber 90
#>
param(
    [ValidateSet('arm64-v8a', 'armeabi-v7a', 'x86_64', 'x86')]
    [string]$Abi = 'arm64-v8a',
    # Номер сборки идёт в versionCode. По умолчанию берётся номер последнего запуска
    # в GitHub Actions: тогда следующая сборка из Actions будет новее локальной.
    [int]$BuildNumber = 0,
    [switch]$Install,
    [switch]$NoMirror,
    [string]$Device = ''
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

$flavors = @{ 'arm64-v8a' = 'Arm64'; 'armeabi-v7a' = 'Armv7'; 'x86_64' = 'X64'; 'x86' = 'X86' }
$flavor = $flavors[$Abi]

# --- JDK 17: на 23 падают проверки и часть плагинов.
$jdk = 'C:\Program Files\Java\jdk-17'
if (-not (Test-Path "$jdk\bin\java.exe")) { throw "Не найден JDK 17: $jdk" }
$env:JAVA_HOME = $jdk
$env:PATH = "$jdk\bin;$env:PATH"

# --- Сабмодули с нативными библиотеками должны быть скачаны.
$missing = git submodule status | Where-Object { $_.StartsWith('-') }
if ($missing) {
    throw "Не скачаны сабмодули (git submodule update --init --recursive --depth 1):`n$($missing -join "`n")"
}

# --- Секреты.
$cfg = @{}
$cfgFile = Join-Path $repo 'local-build.env'
if (Test-Path $cfgFile) {
    foreach ($line in Get-Content $cfgFile -Encoding UTF8) {
        if ($line -match '^\s*([A-Z_]+)\s*=\s*(.*?)\s*$') { $cfg[$Matches[1]] = $Matches[2].Trim('"') }
    }
}
$gradleArgs = @('--build-cache', '--no-configuration-cache', '--parallel', "-PzastoAbiFilter=$Abi")
# Зеркало Google Maven: dl.google.com из некоторых сетей рвёт параллельные загрузки Gradle.
# Отключить: -NoMirror.
if (-not $NoMirror) { $gradleArgs += '-I', "$PSScriptRoot\gradle-mirror.init.gradle" }
if ($cfg['TELEGRAM_API_ID'] -and $cfg['TELEGRAM_API_HASH']) {
    $gradleArgs += "-PtelegramApiId=$($cfg['TELEGRAM_API_ID'])", "-PtelegramApiHash=$($cfg['TELEGRAM_API_HASH'])"
} else {
    Write-Warning 'Нет TELEGRAM_API_ID/HASH в local-build.env: войти в аккаунт в такой сборке не получится.'
}

$secretDir = $null
if ($cfg['KEYSTORE_PATH']) {
    if (-not (Test-Path $cfg['KEYSTORE_PATH'])) { throw "Нет файла ключа: $($cfg['KEYSTORE_PATH'])" }
    if (-not $cfg['KEYSTORE_PASSWORD']) { throw 'В local-build.env нет KEYSTORE_PASSWORD' }
    $secretDir = Join-Path ([IO.Path]::GetTempPath()) ("tgn-sign-" + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $secretDir | Out-Null
    $alias = if ($cfg['KEY_ALIAS']) { $cfg['KEY_ALIAS'] } else { 'zastogram' }
    $keyPass = if ($cfg['KEY_PASSWORD']) { $cfg['KEY_PASSWORD'] } else { $cfg['KEYSTORE_PASSWORD'] }
    [IO.File]::WriteAllText("$secretDir\store", $cfg['KEYSTORE_PASSWORD'])
    [IO.File]::WriteAllText("$secretDir\key", $keyPass)
    [IO.File]::WriteAllText("$secretDir\alias", $alias)
    $gradleArgs += "-PzastoReleaseKeystore=$($cfg['KEYSTORE_PATH'])",
                   "-PzastoReleaseStorePasswordFile=$secretDir\store",
                   "-PzastoReleaseKeyPasswordFile=$secretDir\key",
                   "-PzastoReleaseKeyAliasFile=$secretDir\alias"
    Write-Host 'Подпись: ваш ключ из KEYSTORE_PATH'
} else {
    Write-Warning 'KEYSTORE_PATH не задан: подпись публичным ключом репозитория, поверх сборки из GitHub не встанет.'
}

# --- Номер сборки.
if ($BuildNumber -le 0) {
    $gh = 'C:\Program Files\GitHub CLI\gh.exe'
    if (Test-Path $gh) {
        $n = & $gh run list -R Arseniy07955/telegaNEW -L 1 --json number --jq '.[0].number' 2>$null
        if ($n -match '^\d+$') { $BuildNumber = [int]$n }
    }
}
if ($BuildNumber -le 0) { throw 'Не удалось узнать номер сборки, передай -BuildNumber N' }
$env:ZASTO_BUILD_NUMBER = "$BuildNumber"
Write-Host "Сборка $Abi, номер $BuildNumber"

$task = ":TMessagesProj_AppStandalone:assemble${flavor}Standalone"
$timer = [Diagnostics.Stopwatch]::StartNew()
try {
    # Скачивание зависимостей иногда обрывается на TLS ("SSL peer shut down incorrectly").
    # Gradle докачивает недостающее, поэтому просто повторяем, со второй попытки на TLS 1.2.
    $ok = $false
    for ($attempt = 1; $attempt -le 3 -and -not $ok; $attempt++) {
        $extra = @()
        if ($attempt -gt 1) {
            Write-Host "Попытка $attempt (TLS 1.2)"
            $extra = '-Dorg.gradle.jvmargs=-Xmx8g -XX:MaxMetaspaceSize=1g -Dhttps.protocols=TLSv1.2 -Djdk.tls.client.protocols=TLSv1.2'
        }
        # В репозитории нет gradlew.bat, поэтому запускаем обёртку Gradle напрямую.
        & "$jdk\bin\java.exe" '-Dorg.gradle.appname=gradlew' -classpath "$repo\gradle\wrapper\gradle-wrapper.jar" org.gradle.wrapper.GradleWrapperMain @gradleArgs @extra $task --stacktrace
        $ok = ($LASTEXITCODE -eq 0)
    }
    if (-not $ok) { throw "Gradle завершился с кодом $LASTEXITCODE" }
} finally {
    if ($secretDir -and (Test-Path $secretDir)) { Remove-Item -Recurse -Force $secretDir }
}
$timer.Stop()

$apk = Get-ChildItem "$repo\TMessagesProj_AppStandalone\build\outputs\apk" -Recurse -Filter *.apk |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $apk) { throw 'APK не найден после сборки' }
New-Item -ItemType Directory -Force "$repo\dist-local" | Out-Null
$out = "$repo\dist-local\TelegaNEW-standalone-$Abi.apk"
Copy-Item $apk.FullName $out -Force
Write-Host ("Готово за {0:mm\:ss}: {1} ({2:N1} МБ)" -f $timer.Elapsed, $out, ($apk.Length / 1MB))

if ($Install) {
    $adb = "$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe"
    $adbArgs = @()
    if ($Device) { $adbArgs += '-s', $Device }
    & $adb @adbArgs install -r $out
}
