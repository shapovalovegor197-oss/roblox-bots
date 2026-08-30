<#
  Уводит сеть Roblox мимо VPN-туннеля на уровне маршрутов Windows.

  Почему именно так, а не правилом в конфиге туннеля:

  Правило `process_name -> direct` в sing-box не помогает. Пакет всё равно
  заходит в TUN, sing-box достаёт его оттуда и отправляет наружу уже от своего
  имени, со своим NAT. Для RakNet этого достаточно, чтобы ответ не нашёл дорогу
  обратно. Проверено на живом клиенте: с правилом по процессу ошибка 279
  остаётся, с полностью выключенным VPN — подключается.

  Маршрут в таблице ОС решает это иначе: 128.116.0.0/16 длиннее, чем маршруты
  по умолчанию, которые ставит auto_route, поэтому пакеты Roblox в TUN не
  попадают вообще. Ровно как при выключенном VPN, но только для Roblox.

  И это переживает пересборку конфига Happ при переподключении VPN — в отличие
  от правок в его config.json.

  128.116.0.0/16 — собственная сеть Roblox (AS22697): и API, и игровые серверы
  UDMUX вида 128.116.X.33.

  Roblox у провайдера не заблокирован — проверено, TLS с настоящим SNI проходит
  напрямую и вдвое быстрее туннеля. Если это когда-то изменится, маршрут надо
  будет снять.

  Поставить:  powershell -ExecutionPolicy Bypass -File scripts\roblox-direct-route.ps1
  Снять:      powershell -ExecutionPolicy Bypass -File scripts\roblox-direct-route.ps1 -Remove
  Показать:   powershell -ExecutionPolicy Bypass -File scripts\roblox-direct-route.ps1 -Show
#>
param([switch]$Remove, [switch]$Show)

$ErrorActionPreference = "Stop"
$Net = "128.116.0.0"
$Mask = "255.255.0.0"
$Prefix = "128.116.0.0/16"

function Get-PhysicalGateway {
    $r = Get-NetRoute -DestinationPrefix "0.0.0.0/0" -ErrorAction SilentlyContinue |
         Where-Object {
             $a = Get-NetAdapter -InterfaceIndex $_.ifIndex -ErrorAction SilentlyContinue
             $a -and $a.InterfaceDescription -notmatch "tun|TAP|WARP|Cloudflare|Outline"
         } | Sort-Object RouteMetric | Select-Object -First 1
    if (-not $r) { throw "Не нашёл физический шлюз. VPN включён без Ethernet?" }
    return @{ Gateway = $r.NextHop; IfIndex = $r.ifIndex }
}

function Show-State {
    "=== маршрут до сети Roblox ($Prefix) ==="
    $rt = Get-NetRoute -DestinationPrefix $Prefix -ErrorAction SilentlyContinue
    if ($rt) {
        foreach ($r in $rt) {
            $n = (Get-NetAdapter -InterfaceIndex $r.ifIndex -ErrorAction SilentlyContinue).Name
            "  через $($r.NextHop)  интерфейс $n  метрика $($r.RouteMetric)"
        }
    } else {
        "  отдельного маршрута нет — трафик Roblox идёт по умолчанию, то есть в туннель"
    }
    ""
    "=== маршруты по умолчанию ==="
    Get-NetRoute -DestinationPrefix "0.0.0.0/0" | ForEach-Object {
        $n = (Get-NetAdapter -InterfaceIndex $_.ifIndex -ErrorAction SilentlyContinue).Name
        $im = (Get-NetIPInterface -InterfaceIndex $_.ifIndex -AddressFamily IPv4).InterfaceMetric
        "  {0,-14} шлюз={1,-15} итоговая метрика={2}" -f $n, $_.NextHop, ($_.RouteMetric + $im)
    }
}

if ($Show) { Show-State; return }

if ($Remove) {
    $existing = Get-NetRoute -DestinationPrefix $Prefix -ErrorAction SilentlyContinue
    if ($existing) {
        route delete $Net mask $Mask | Out-Null
        "Маршрут снят. Трафик Roblox снова пойдёт через туннель."
    } else {
        "Маршрута и не было."
    }
    ""
    Show-State
    return
}

$gw = Get-PhysicalGateway
"Физический шлюз: $($gw.Gateway)  (интерфейс $((Get-NetAdapter -InterfaceIndex $gw.IfIndex).Name))"

# -p делает маршрут постоянным: переживёт перезагрузку и переподключение VPN
route -p add $Net mask $Mask $gw.Gateway metric 1 if $gw.IfIndex | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "route add вернул $LASTEXITCODE. Нужен запуск от администратора."
}
"Маршрут добавлен: $Prefix → напрямую, мимо туннеля."
""
Show-State
