Add-Type -AssemblyName System.Drawing

$jsonContent = Get-Content -Raw "scripts/simplified_loops.json"
$jsonLoops = $jsonContent | ConvertFrom-Json

function Draw-HoodieCat {
    param(
        [System.Drawing.Graphics]$g,
        [float]$targetX,
        [float]$targetY,
        [float]$targetH
    )

    $scale = $targetH / 247.0
    $ox = $targetX - (30.0 * $scale)
    $oy = $targetY - (38.0 * $scale)

    $savedState = $g.Save()
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality

    $colWhite = [System.Drawing.Color]::FromArgb(255, 240, 246, 252)
    $colDark  = [System.Drawing.Color]::FromArgb(255, 8, 12, 20)

    $brushWhite = New-Object System.Drawing.SolidBrush $colWhite
    $brushDark  = New-Object System.Drawing.SolidBrush $colDark

    foreach ($idx in @(0, 2, 1, 4, 5)) {
        $loop = $jsonLoops[$idx]
        $path = New-Object System.Drawing.Drawing2D.GraphicsPath
        $pts = New-Object System.Collections.Generic.List[System.Drawing.PointF]
        foreach ($p in $loop.points) {
            $px = [float]$p[0] * $scale + $ox
            $py = [float]$p[1] * $scale + $oy
            $pts.Add((New-Object System.Drawing.PointF $px, $py))
        }
        $path.AddPolygon($pts.ToArray())
        $g.FillPath($brushWhite, $path)
        $path.Dispose()
    }

    # Nose (Dark Cutout)
    $loopNose = $jsonLoops[3]
    $pathNose = New-Object System.Drawing.Drawing2D.GraphicsPath
    $ptsNose = New-Object System.Collections.Generic.List[System.Drawing.PointF]
    foreach ($p in $loopNose.points) {
        $px = [float]$p[0] * $scale + $ox
        $py = [float]$p[1] * $scale + $oy
        $ptsNose.Add((New-Object System.Drawing.PointF $px, $py))
    }
    $pathNose.AddPolygon($ptsNose.ToArray())
    $g.FillPath($brushDark, $pathNose)
    $pathNose.Dispose()

    $brushWhite.Dispose()
    $brushDark.Dispose()
    $g.Restore($savedState)
}

# 1. Generate logo-icon.png (128x128)
$iconBmp = New-Object System.Drawing.Bitmap 128, 128
$gIcon = [System.Drawing.Graphics]::FromImage($iconBmp)
$gIcon.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$gIcon.Clear([System.Drawing.Color]::Transparent)

# Centered: Mascot height = 112px
$targetH = 112.0
$targetW = 198.0 * ($targetH / 247.0)
$startX = (128.0 - $targetW) / 2.0
$startY = (128.0 - $targetH) / 2.0
Draw-HoodieCat $gIcon $startX $startY $targetH

$iconPath = "src/github_intelligence/dashboard_assets/logo-icon.png"
$iconBmp.Save($iconPath, [System.Drawing.Imaging.ImageFormat]::Png)
$gIcon.Dispose()
$iconBmp.Dispose()
Write-Output ("Saved " + $iconPath + " (" + (Get-Item $iconPath).Length + " bytes)")

# 2. Generate logo.png (360x90) for Dashboard
$logoBmp = New-Object System.Drawing.Bitmap 360, 90
$gLogo = [System.Drawing.Graphics]::FromImage($logoBmp)
$gLogo.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$gLogo.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit
$gLogo.Clear([System.Drawing.Color]::Transparent)

# Draw Hoodie Cat mascot on left <<
$logoH = 72.0
$logoStartY = (90.0 - $logoH) / 2.0
Draw-HoodieCat $gLogo 18.0 $logoStartY $logoH

# High-Contrast Monochrome Typography >>>
$fontMain = New-Object System.Drawing.Font("Segoe UI", 26, [System.Drawing.FontStyle]::Bold, [System.Drawing.GraphicsUnit]::Pixel)
$fontSub  = New-Object System.Drawing.Font("Segoe UI", 21, [System.Drawing.FontStyle]::Bold, [System.Drawing.GraphicsUnit]::Pixel)

$brushWhite = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255, 255, 255, 255))
$brushSlate = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255, 203, 213, 225))

$gLogo.DrawString("GitHub", $fontMain, $brushWhite, 96, 12)
$gLogo.DrawString("Intelligence", $fontSub, $brushSlate, 96, 44)

$logoPath = "src/github_intelligence/dashboard_assets/logo.png"
$logoBmp.Save($logoPath, [System.Drawing.Imaging.ImageFormat]::Png)

# Also update docs/logo.png
$docsLogoPath = "docs/logo.png"
$logoBmp.Save($docsLogoPath, [System.Drawing.Imaging.ImageFormat]::Png)

$fontMain.Dispose()
$fontSub.Dispose()
$brushWhite.Dispose()
$brushSlate.Dispose()
$gLogo.Dispose()
$logoBmp.Dispose()

Write-Output ("Saved " + $logoPath + " (" + (Get-Item $logoPath).Length + " bytes)")
Write-Output ("Saved " + $docsLogoPath + " (" + (Get-Item $docsLogoPath).Length + " bytes)")
