# 调用 Windows 自带 OCR（Windows.Media.Ocr）识别一张图片，结果写成 JSON。
# 用途：S1-1 合成样本的可读性自检（"字真的画出来了"），以及后续对真实票据做票种探测。
#
# 注意：本脚本会把识别到的文本写入 OutFile。**只应对合成样本或已脱敏图片使用**，
# 真实票据的识别结果含个人信息，不要落盘、不要提交。
#
# 用法：
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts/ocr_probe.ps1 -Path a.jpg -OutFile out.json
param(
  [Parameter(Mandatory = $true)][string]$Path,
  [Parameter(Mandatory = $true)][string]$OutFile
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Storage.StorageFile, Windows.Foundation, ContentType = WindowsRuntime]

$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]

function Await($op, $type) {
  $task = $asTask.MakeGenericMethod($type).Invoke($null, @($op))
  $task.Wait(30000) | Out-Null
  $task.Result
}

$full = (Resolve-Path -LiteralPath $Path).Path
$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($full)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])

$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if (-not $engine) {
  @{ ok = $false; error = "OCR_ENGINE_UNAVAILABLE"; lines = @() } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $OutFile -Encoding UTF8
  exit 2
}

$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])

$lines = @()
foreach ($line in $result.Lines) {
  $words = @()
  foreach ($word in $line.Words) {
    $r = $word.BoundingRect
    $words += [ordered]@{ text = $word.Text; x = [math]::Round($r.X); y = [math]::Round($r.Y); w = [math]::Round($r.Width); h = [math]::Round($r.Height) }
  }
  $lines += [ordered]@{ text = $line.Text; words = $words }
}

@{ ok = $true; engine = "Windows.Media.Ocr"; line_count = $lines.Count; lines = $lines } |
  ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $OutFile -Encoding UTF8
exit 0