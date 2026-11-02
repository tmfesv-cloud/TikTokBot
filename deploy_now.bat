@echo off
setlocal
set "REPO=C:\Users\times\Documents\GitHub\TikTokBot"
set "SRC=C:\testbat\TikTokBot"

echo [1/4] Copy files to repo...
copy /Y "%SRC%\app\services\tiktok_service.py" "%REPO%\app\services\" >nul
copy /Y "%SRC%\config.py" "%REPO%\" >nul
copy /Y "%SRC%\.env.example" "%REPO%\" >nul

echo [2/4] Commit...
cd /d "%REPO%"
git add -A
git commit -m "Deploy TikTokBot update"

echo [3/4] Push...
git push

echo [4/4] DONE! Render redeploys automatically in 1-2 min.
echo Now send the bot a YouTube link and check.
pause
