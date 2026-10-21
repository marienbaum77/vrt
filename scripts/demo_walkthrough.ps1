# Demo walkthrough script for screen recording / screencast
# Run: powershell -ExecutionPolicy Bypass -File scripts\demo_walkthrough.ps1

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " VRT: Сквозная демонстрация для записи скринкаста (3-4 мин) " -ForegroundColor Cyan
Write-Host " Нажмите Win + Alt + R для старта записи экрана в Windows!  " -ForegroundColor Yellow
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host ""
pause

# Шаг 1: Статус Docker контейнеров
Clear-Host
Write-Host ">>> ШАГ 1: Проверка запущенных сервисов Docker Compose" -ForegroundColor Green
docker compose ps
Write-Host ""
Write-Host "[Спикер: 'Все сервисы развернуты в Docker: demo-site, mock-ci, vrt-api и prometheus']" -ForegroundColor DarkGray
Write-Host ""
pause

# Шаг 2: Снятие эталонов (Baseline) с v1
Clear-Host
Write-Host ">>> ШАГ 2: Снятие эталонных скриншотов (Baseline) с версии сайта /v1" -ForegroundColor Green
docker compose run --rm -e VRT_BASE_URL=http://demo-site/v1 runner vrt baseline -c examples/vrt.docker.yaml
Write-Host ""
Write-Host "[Спикер: 'Сняты 3 эталона: десктоп, мобильный вид и страница pricing']" -ForegroundColor DarkGray
Write-Host ""
pause

# Шаг 3: Тестирование версии с изменениями (/v2)
Clear-Host
Write-Host ">>> ШАГ 3: Запуск VRT-проверки против обновленной версии /v2 (содержит изменения)" -ForegroundColor Green
docker compose run --rm -e VRT_BASE_URL=http://demo-site/v2 runner
Write-Host ""
Write-Host "[Спикер: 'Тест зафиксировал визуальные регрессии и завершился с кодом 1 (FAILED)']" -ForegroundColor DarkGray
Write-Host ""
pause

# Шаг 4: Экспорт и открытие HTML-отчета
Clear-Host
Write-Host ">>> ШАГ 4: Открытие сгенерированного интерактивного HTML-отчета" -ForegroundColor Green
docker cp vrt-vrt-api-1:/data/report ./report
Start-Process ".\report\index.html"
Write-Host "Отчет открыт в браузере. Продемонстрируйте интерактивный слайдер swipe и diff." -ForegroundColor Yellow
Write-Host ""
Write-Host "[Спикер: 'Открываем HTML-отчет, двигаем слайдер между baseline и candidate']" -ForegroundColor DarkGray
Write-Host ""
pause

# Шаг 5: Проверка статуса в Mock CI (Блокировка merge)
Clear-Host
Write-Host ">>> ШАГ 5: Проверка статуса в Mock CI (блокировка PR)" -ForegroundColor Green
$state = Invoke-RestMethod -Uri "http://localhost:9000/__state"
Write-Host "Последний статус проверки в CI:" -ForegroundColor Cyan
$state.statuses | Select-Object -Last 1 | Format-List repo, state, description
Write-Host "Комментарий бота в Pull Request:" -ForegroundColor Cyan
$state.comments."acme/site#7" | Select-Object -ExpandProperty body
Write-Host ""
Write-Host "[Спикер: 'CI заблокировал слияние PR (статус failure) и оставил отчет в комментариях']" -ForegroundColor DarkGray
Write-Host ""
pause

# Шаг 6: Approve и разблокировка merge
Clear-Host
Write-Host ">>> ШАГ 6: Одобрение изменений через vrt approve" -ForegroundColor Green
docker compose run --rm runner vrt approve -c examples/vrt.docker.yaml
Write-Host "Повторный прогон теста против /v2:" -ForegroundColor Cyan
docker compose run --rm -e VRT_BASE_URL=http://demo-site/v2 runner
Write-Host ""
Write-Host "[Спикер: 'После approve эталоны обновлены, повторный тест вернул PASSED, merge разрешен!']" -ForegroundColor DarkGray
Write-Host ""
pause

# Шаг 7: Открытие метрик в Prometheus
Clear-Host
Write-Host ">>> ШАГ 7: Метрики Prometheus" -ForegroundColor Green
Start-Process "http://localhost:9090/targets"
Write-Host "Prometheus открыт в браузере. Таргет vrt-api в состоянии UP." -ForegroundColor Yellow
Write-Host ""
Write-Host "Демонстрация завершена! Нажмите Win + Alt + R для остановки записи." -ForegroundColor Green
