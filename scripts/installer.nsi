; GPredict-Improved NSIS Installer Script
; Requires NSIS 3.x (available on GitHub Actions windows-latest runner)
;
; Usage:
;   makensis scripts\installer.nsi
;
; Produces:
;   dist\GPredict-Improved-Setup.exe

Unicode True

; ---- Definitions -------------------------------------------------------
!define APP_NAME        "GPredict-Improved"
!define APP_EXE         "gpredict-improved.exe"
!define PUBLISHER       "GPredict-Improved Project"
!define GITHUB_URL      "https://github.com/JF9SOM/gpredict-improved"
!define UNINSTALL_KEY   "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}"
!define REG_INSTALL_DIR "Software\${APP_NAME}"

; Version is passed from CI via /DAPP_VERSION=x.y.z
!ifndef APP_VERSION
  !define APP_VERSION "0.0.0"
!endif

; ---- General -----------------------------------------------------------
Name            "${APP_NAME} ${APP_VERSION}"
OutFile         "dist\GPredict-Improved-Setup.exe"
InstallDir      "$PROGRAMFILES64\${APP_NAME}"
InstallDirRegKey HKLM "${REG_INSTALL_DIR}" "InstallDir"
RequestExecutionLevel admin
SetCompressor   /SOLID lzma

; ---- Modern UI ---------------------------------------------------------
!include "MUI2.nsh"

!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_RUN         "$INSTDIR\${APP_EXE}"
!define MUI_FINISHPAGE_RUN_TEXT    "Launch ${APP_NAME}"
!define MUI_FINISHPAGE_LINK        "Visit project on GitHub"
!define MUI_FINISHPAGE_LINK_LOCATION "${GITHUB_URL}"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"

; ---- Version resource (visible in file properties) --------------------
VIProductVersion "${APP_VERSION}.0"
VIAddVersionKey "ProductName"     "${APP_NAME}"
VIAddVersionKey "ProductVersion"  "${APP_VERSION}"
VIAddVersionKey "CompanyName"     "${PUBLISHER}"
VIAddVersionKey "FileVersion"     "${APP_VERSION}.0"
VIAddVersionKey "FileDescription" "${APP_NAME} Installer"

; ---- Install section ---------------------------------------------------
Section "Install" SecMain

  ; Kill running instance gracefully before overwriting files
  ExecWait 'taskkill /IM "${APP_EXE}" /F' $0

  SetOutPath "$INSTDIR"
  File /r "dist\gpredict-improved\*.*"

  ; Store install dir in registry
  WriteRegStr HKLM "${REG_INSTALL_DIR}" "InstallDir" "$INSTDIR"
  WriteRegStr HKLM "${REG_INSTALL_DIR}" "Version"    "${APP_VERSION}"

  ; Shortcuts
  CreateDirectory "$SMPROGRAMS\${APP_NAME}"
  CreateShortcut  "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk" \
                  "$INSTDIR\${APP_EXE}" "" "$INSTDIR\${APP_EXE}" 0
  CreateShortcut  "$DESKTOP\${APP_NAME}.lnk" \
                  "$INSTDIR\${APP_EXE}" "" "$INSTDIR\${APP_EXE}" 0

  ; Uninstaller
  WriteUninstaller "$INSTDIR\uninstall.exe"

  ; Add/Remove Programs entry
  WriteRegStr   HKLM "${UNINSTALL_KEY}" "DisplayName"          "${APP_NAME}"
  WriteRegStr   HKLM "${UNINSTALL_KEY}" "DisplayVersion"       "${APP_VERSION}"
  WriteRegStr   HKLM "${UNINSTALL_KEY}" "Publisher"            "${PUBLISHER}"
  WriteRegStr   HKLM "${UNINSTALL_KEY}" "URLInfoAbout"         "${GITHUB_URL}"
  WriteRegStr   HKLM "${UNINSTALL_KEY}" "InstallLocation"      "$INSTDIR"
  WriteRegStr   HKLM "${UNINSTALL_KEY}" "UninstallString"      "$INSTDIR\uninstall.exe"
  WriteRegStr   HKLM "${UNINSTALL_KEY}" "DisplayIcon"          "$INSTDIR\${APP_EXE}"
  WriteRegDWORD HKLM "${UNINSTALL_KEY}" "NoModify"             1
  WriteRegDWORD HKLM "${UNINSTALL_KEY}" "NoRepair"             1

SectionEnd

; ---- Uninstall section -------------------------------------------------
Section "Uninstall"

  ; Kill running instance before removing files
  ExecWait 'taskkill /IM "${APP_EXE}" /F' $0

  RMDir /r "$INSTDIR"

  Delete "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk"
  RMDir  "$SMPROGRAMS\${APP_NAME}"
  Delete "$DESKTOP\${APP_NAME}.lnk"

  DeleteRegKey HKLM "${UNINSTALL_KEY}"
  DeleteRegKey HKLM "${REG_INSTALL_DIR}"

SectionEnd
