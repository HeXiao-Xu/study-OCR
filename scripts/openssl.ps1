# 在 Docker 里执行 OpenSSL 3.x（与 scripts/openssl.sh 等价，给 Windows PowerShell 用）。
#
# 为什么：宿主机没有 openssl.exe（PATH / MSYS2 / Git 都没有），Docker 是本项目既有依赖；
# 用容器里的 OpenSSL 3.5 既满足"OpenSSL 3.x 命令行"，又不用在宿主机装任何东西（D-078）。
#
# 用法（路径一律用**仓库相对路径**；容器内工作目录 = /work = 仓库根）：
#   powershell -NoProfile -File scripts/openssl.ps1 version
#   powershell -NoProfile -File scripts/openssl.ps1 genpkey -algorithm SM2 -out secrets/keys/alice.pem
#   powershell -NoProfile -File scripts/openssl.ps1 dgst -sm3 -binary somefile
#
# 镜像可用环境变量覆盖（换镜像源/离线内网时用）：
#   $env:OPENSSL_IMAGE = "registry.example.com/alpine/openssl:3.5.9"
#
# 注意：本脚本不声明 param()，就是为了让 `-out` 之类的参数不被 PowerShell 当成
# 自己的通用参数（-OutVariable/-OutBuffer）解析；所有参数原样进 $args。
$ErrorActionPreference = "Stop"
$opensslArgs = $args
$root = Split-Path -Parent $PSScriptRoot
$image = if ($env:OPENSSL_IMAGE) { $env:OPENSSL_IMAGE } else { "alpine/openssl:3.5.9" }

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error "找不到 docker：本机没有 openssl CLI，密钥/证书由 Docker 里的 OpenSSL 生成（D-078）。"
    exit 1
}

docker run --rm --network none -v "${root}:/work" -w /work --entrypoint openssl $image @opensslArgs
exit $LASTEXITCODE
