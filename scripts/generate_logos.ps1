Add-Type -AssemblyName System.Drawing

function Draw-GitCat {
    param(
        [System.Drawing.Graphics]$g,
        [float]$cx,
        [float]$cy,
        [float]$scale
    )
    
    $penCyan = New-Object System.Drawing.Pen ([System.Drawing.Color]::FromArgb(255, 56, 189, 248)), (2.6 * $scale)
    $penCyan.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
    $penCyan.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
    $penCyan.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round
    
    $penSlate = New-Object System.Drawing.Pen ([System.Drawing.Color]::FromArgb(255, 148, 163, 184)), (2.0 * $scale)
    $penSlate.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
    $penSlate.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
    $penSlate.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round

    $brushCyan = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255, 56, 189, 248))
    $brushEmerald = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255, 16, 185, 129))
    $brushDark = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255, 13, 17, 23))

    function P([float]$x, [float]$y) {
        return New-Object System.Drawing.PointF ($cx + ($x - 64) * $scale), ($cy + ($y - 64) * $scale)
    }

    # Head Outline / Ears
    $headPoints = @(
        (P 64 36), (P 46 38), (P 26 22), (P 24 54), (P 18 72),
        (P 28 92), (P 44 104), (P 64 112), (P 84 104), (P 100 92),
        (P 110 72), (P 104 54), (P 102 22), (P 82 38), (P 64 36)
    )
    $g.DrawLines($penCyan, $headPoints)

    # Inner Ear Geometrics
    $leftEarInner = @((P 32 36), (P 28 58), (P 44 48), (P 32 36))
    $g.DrawLines($penSlate, $leftEarInner)
    $rightEarInner = @((P 96 36), (P 100 58), (P 84 48), (P 96 36))
    $g.DrawLines($penSlate, $rightEarInner)

    # Eyes (Angular Feline Tech)
    $leftEye = @((P 36 64), (P 50 62), (P 44 70), (P 36 64))
    $g.FillPolygon($brushCyan, $leftEye)
    $rightEye = @((P 92 64), (P 78 62), (P 84 70), (P 92 64))
    $g.FillPolygon($brushCyan, $rightEye)

    # Git Commit Topology (Branches)
    $g.DrawLine($penSlate, (P 64 36), (P 64 52))
    $g.DrawLine($penSlate, (P 64 52), (P 46 80))
    $g.DrawLine($penSlate, (P 64 52), (P 82 80))
    $g.DrawLine($penSlate, (P 46 80), (P 64 98))
    $g.DrawLine($penSlate, (P 82 80), (P 64 98))
    $g.DrawLine($penSlate, (P 64 98), (P 64 112))

    # Whiskers
    $g.DrawLine($penCyan, (P 32 78), (P 10 76))
    $g.DrawLine($penCyan, (P 34 86), (P 14 90))
    $g.DrawLine($penCyan, (P 96 78), (P 118 76))
    $g.DrawLine($penCyan, (P 94 86), (P 114 90))

    # Commit Nodes
    function DrawNode([float]$x, [float]$y, [System.Drawing.SolidBrush]$brush, [float]$r) {
        $pt = P $x $y
        $rad = $r * $scale
        $rect = New-Object System.Drawing.RectangleF ($pt.X - $rad), ($pt.Y - $rad), ($rad * 2), ($rad * 2)
        $g.FillEllipse($brushDark, $rect)
        $g.FillEllipse($brush, (New-Object System.Drawing.RectangleF ($pt.X - $rad * 0.7), ($pt.Y - $rad * 0.7), ($rad * 1.4), ($rad * 1.4)))
        $g.DrawEllipse($penCyan, $rect)
    }

    DrawNode 64 36 $brushCyan 4.5
    DrawNode 64 52 $brushEmerald 4.5
    DrawNode 46 80 $brushCyan 4.5
    DrawNode 82 80 $brushCyan 4.5
    DrawNode 64 98 $brushEmerald 4.5
    DrawNode 64 112 $brushCyan 3.5

    # Nose / Muzzle
    $muzzle = @((P 64 74), (P 61 70), (P 67 70), (P 64 74))
    $g.FillPolygon($brushCyan, $muzzle)

    $penCyan.Dispose()
    $penSlate.Dispose()
    $brushCyan.Dispose()
    $brushEmerald.Dispose()
    $brushDark.Dispose()
}

# 1. Generate logo-icon.png (128x128)
$iconBmp = New-Object System.Drawing.Bitmap 128, 128
$gIcon = [System.Drawing.Graphics]::FromImage($iconBmp)
$gIcon.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$gIcon.Clear([System.Drawing.Color]::Transparent)
Draw-GitCat $gIcon 64 64 1.05
$iconPath = "src/github_intelligence/dashboard_assets/logo-icon.png"
$iconBmp.Save($iconPath, [System.Drawing.Imaging.ImageFormat]::Png)
$gIcon.Dispose()
$iconBmp.Dispose()
Write-Output ("Saved " + $iconPath + " (" + (Get-Item $iconPath).Length + " bytes)")

# 2. Generate logo.png (360x90)
$logoBmp = New-Object System.Drawing.Bitmap 360, 90
$gLogo = [System.Drawing.Graphics]::FromImage($logoBmp)
$gLogo.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$gLogo.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit
$gLogo.Clear([System.Drawing.Color]::Transparent)

# Draw cat mascot on the left
Draw-GitCat $gLogo 46 45 0.72

$fontMain = New-Object System.Drawing.Font("Segoe UI", 20, [System.Drawing.FontStyle]::Bold, [System.Drawing.GraphicsUnit]::Pixel)
$fontSub = New-Object System.Drawing.Font("Segoe UI", 16, [System.Drawing.FontStyle]::Bold, [System.Drawing.GraphicsUnit]::Pixel)
$fontLabel = New-Object System.Drawing.Font("Segoe UI", 8, [System.Drawing.FontStyle]::Bold, [System.Drawing.GraphicsUnit]::Pixel)

$brushWhite = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255, 240, 246, 252))
$brushCyan = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255, 56, 189, 248))
$brushMuted = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255, 139, 148, 158))

$gLogo.DrawString("GitHub", $fontMain, $brushWhite, 95, 14)
$gLogo.DrawString("Intelligence", $fontSub, $brushCyan, 95, 40)
$gLogo.DrawString("EVIDENCE & AUDIT", $fontLabel, $brushMuted, 96, 64)

$logoPath = "src/github_intelligence/dashboard_assets/logo.png"
$logoBmp.Save($logoPath, [System.Drawing.Imaging.ImageFormat]::Png)

$fontMain.Dispose()
$fontSub.Dispose()
$fontLabel.Dispose()
$brushWhite.Dispose()
$brushCyan.Dispose()
$brushMuted.Dispose()
$gLogo.Dispose()
$logoBmp.Dispose()
Write-Output ("Saved " + $logoPath + " (" + (Get-Item $logoPath).Length + " bytes)")
