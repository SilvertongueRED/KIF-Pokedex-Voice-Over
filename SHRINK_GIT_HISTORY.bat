@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo   KIF Pokedex Voice Over - Git history shrink
echo ============================================================
echo This permanently removes two folders that were committed long
echo ago and later deleted, but still bloat the repo's .git store:
echo     - piper_training_data\   (old Piper training WAVs, ~350 MB)
echo     - tools\                 (old Piper tooling)
echo Your commit history (messages / timeline) is preserved.
echo Commit hashes WILL change; if you have a GitHub remote you must
echo force-push afterwards (instructions print at the end).
echo.

git rev-parse --is-inside-work-tree >nul 2>nul
if errorlevel 1 (
  echo ERROR: this folder is not a git repository.
  pause & exit /b 1
)

REM Refuse to run with a dirty tree (commit COMMIT_slim_offline.bat first).
set "DIRTY="
for /f "delims=" %%i in ('git status --porcelain') do set "DIRTY=1"
if defined DIRTY (
  echo ERROR: you have uncommitted changes.
  echo        Run COMMIT_slim_offline.bat first, then re-run this script.
  pause & exit /b 1
)

echo Current .git size:
powershell -NoProfile -Command "$s=(Get-ChildItem -Recurse -Force '.git' | Measure-Object -Property Length -Sum).Sum; '   {0:N1} MB' -f ($s/1MB)"
echo.

for /f %%t in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set "STAMP=%%t"
set "BACKUP=..\KIF-PVO-history-backup-!STAMP!.bundle"
echo Creating full safety backup: !BACKUP!
git bundle create "!BACKUP!" --all
if errorlevel 1 ( echo ERROR: backup failed - aborting, nothing changed. & pause & exit /b 1 )
git tag "backup/pre-shrink-!STAMP!" >nul 2>nul
echo Backup created. To recover later:  git clone "!BACKUP!" recovered-repo
echo.

set "GO="
set /p GO=Type  YES  to rewrite history now: 
if /i not "!GO!"=="YES" ( echo Aborted. No changes made. & pause & exit /b 0 )

echo.
echo Rewriting history (a few minutes is normal)...
set "FILTER_BRANCH_SQUELCH_WARNING=1"
git filter-branch --force --prune-empty ^
    --index-filter "git rm -r --cached --ignore-unmatch piper_training_data tools" ^
    --tag-name-filter cat -- --all
if errorlevel 1 ( echo ERROR: filter-branch failed. Your backup bundle is safe. & pause & exit /b 1 )

echo.
echo Cleaning original refs and repacking...
git for-each-ref --format="delete %%(refname)" refs/original/ > "%TEMP%\pvo_delrefs.txt"
git update-ref --stdin < "%TEMP%\pvo_delrefs.txt"
del "%TEMP%\pvo_delrefs.txt" >nul 2>nul
git reflog expire --expire=now --all
git gc --prune=now --aggressive

echo.
echo New .git size:
powershell -NoProfile -Command "$s=(Get-ChildItem -Recurse -Force '.git' | Measure-Object -Property Length -Sum).Sum; '   {0:N1} MB' -f ($s/1MB)"
echo.
echo Done. History preserved; the two old folders are gone from every commit.
echo.
echo If you push to GitHub, update the remote with a force push:
echo     git push --force --all
echo     git push --force --tags
echo (Anyone who already cloned must re-clone after this.)
echo.
pause
endlocal
