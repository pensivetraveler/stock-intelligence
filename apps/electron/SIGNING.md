# macOS 서명·공증 절차

이 저장소는 서명 설정만 준비해 둔다. 인증서·비밀번호·API 키는 저장소, `.env`, 로그, 커밋 어디에도 두지 않는다. 아래는 사용자가 직접 하는 일이다. 결정 기록은 [ADR 0028](../../docs/decisions/0028-electron-desktop-shell.md)에 있다.

환경 변수가 없으면 `npm run dist`는 지금처럼 서명하지 않은 `.dmg`를 만든다.

## 1. 한 번만 준비할 것

1. **Apple Developer Program 가입**(유료). 팀 ID(10자리)를 developer.apple.com의 Membership에서 확인한다.
2. **Developer ID Application 인증서** 만들기. Xcode → Settings → Accounts → Manage Certificates → `+` → "Developer ID Application"을 고른다. 인증서가 로그인 키체인에 들어간다.
   - 확인: `security find-identity -v -p codesigning` 출력에 `Developer ID Application: 이름 (팀ID)`가 보여야 한다.
3. **공증 자격 증명** 중 하나를 고른다. 첫 번째를 권한다.
   - **App Store Connect API 키:** App Store Connect → 사용자 및 액세스 → 통합 → 키에서 "Developer" 권한 키를 만들고 `.p8` 파일을 받는다. 키 ID와 Issuer ID를 적어 둔다. `.p8`은 저장소 밖(예: `~/.private_keys/`)에 둔다.
   - **키체인 프로필:** `xcrun notarytool store-credentials siw-notary --apple-id <Apple ID> --team-id <팀ID>`를 실행하고, 묻는 앱 암호(appleid.apple.com에서 만든 앱 전용 암호)를 입력한다. 암호는 키체인에만 저장된다.
   - **Apple ID + 앱 전용 암호:** 환경 변수에 암호가 남으므로 권하지 않는다.

## 2. 서명·공증 빌드

`apps/electron`에서, 같은 터미널 세션에만 환경 변수를 둔다(셸 설정 파일에 적지 않는다).

API 키를 쓰는 경우:

```bash
CSC_NAME="Developer ID Application: 이름 (팀ID)" APPLE_API_KEY="$HOME/.private_keys/AuthKey_XXXXXXXXXX.p8" APPLE_API_KEY_ID="XXXXXXXXXX" APPLE_API_ISSUER="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" npm run dist
```

키체인 프로필을 쓰는 경우:

```bash
CSC_NAME="Developer ID Application: 이름 (팀ID)" APPLE_KEYCHAIN_PROFILE="siw-notary" npm run dist
```

- `CSC_NAME`은 앞의 `Developer ID Application: ` 접두어를 빼고 써도 된다.
- 키체인 대신 내보낸 `.p12`를 쓰려면 `CSC_NAME` 대신 `CSC_LINK=<.p12 경로>`와 `CSC_KEY_PASSWORD`를 준다.
- 서명 변수만 있고 공증 변수가 없으면 서명만 하고 공증은 건너뛴다(Gatekeeper 경고는 남는다).

## 3. 확인

```bash
codesign --verify --deep --strict --verbose=2 "release/mac/Stock Intelligence Workstation.app"
```

```bash
spctl --assess --type execute --verbose "release/mac/Stock Intelligence Workstation.app"
```

`spctl` 결과가 `accepted` / `source=Notarized Developer ID`이면 공증까지 끝난 것이다. 결과를 `docs/PROJECT_STATE.md`에 기록한다.

## 참고

- 서명은 hardened runtime으로 하며, 권한은 `assets/entitlements.mac.plist`의 `com.apple.security.cs.allow-jit` 하나다(Electron V8용).
- 지금 빌드는 x64 전용이다. Apple Silicon 기본 실행이 필요하면 `--universal` 빌드를 따로 검토한다.
- 설정 위치: `electron-builder.config.cjs`.
