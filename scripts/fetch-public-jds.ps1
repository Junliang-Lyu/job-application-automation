[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$UrlListPath,

    [Parameter(Mandatory)]
    [string]$OutputPath,

    [ValidateRange(1, 120)]
    [int]$TimeoutSeconds = 20
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function New-Record {
    param(
        [string]$SourceUrl,
        [string]$NormalizedUrl = $SourceUrl,
        [string]$Status,
        [string]$Error,
        [string]$Title = $null,
        [string]$Text = $null,
        [string]$ContentHash = $null,
        [string]$FetchedAt = $null
    )

    [pscustomobject]@{
        sourceUrl = $SourceUrl
        normalizedUrl = $NormalizedUrl
        title = $Title
        text = $Text
        contentHash = $ContentHash
        fetchedAt = $FetchedAt
        status = $Status
        error = $Error
    }
}

function ConvertFrom-JobHtml {
    param([string]$Html)

    $withoutNonVisibleNodes = [regex]::Replace($Html, '(?is)<(script|style|noscript)[^>]*>.*?</\1>', ' ')
    $titleMatch = [regex]::Match($withoutNonVisibleNodes, '(?is)<title[^>]*>(.*?)</title>')
    $title = if ($titleMatch.Success) {
        [regex]::Replace([System.Net.WebUtility]::HtmlDecode($titleMatch.Groups[1].Value), '\s+', ' ').Trim()
    } else {
        $null
    }
    $withoutTitle = [regex]::Replace($withoutNonVisibleNodes, '(?is)<title[^>]*>.*?</title>', ' ')
    $visibleText = [regex]::Replace($withoutTitle, '(?is)<[^>]+>', ' ')
    $text = [regex]::Replace([System.Net.WebUtility]::HtmlDecode($visibleText), '\s+', ' ').Trim()

    [pscustomobject]@{ Title = $title; Text = $text }
}

function Get-Sha256Hex {
    param([string]$Text)

    $bytes = [System.Text.Encoding]::UTF8.GetBytes($Text)
    $hash = [System.Security.Cryptography.SHA256]::Create().ComputeHash($bytes)
    ([System.BitConverter]::ToString($hash)).Replace('-', '').ToLowerInvariant()
}

if (-not (Test-Path -LiteralPath $UrlListPath -PathType Leaf)) {
    throw "URL list not found: $UrlListPath"
}

$records = [System.Collections.Generic.List[object]]::new()
$seenUrls = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
$seenContentHashes = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
foreach ($line in Get-Content -LiteralPath $UrlListPath) {
    $url = $line.Trim()
    if ([string]::IsNullOrWhiteSpace($url) -or $url.StartsWith('#')) {
        continue
    }

    $uri = $null
    if (-not [uri]::TryCreate($url, [uriKind]::Absolute, [ref]$uri) -or $uri.Scheme -notin @('http', 'https')) {
        [void]$records.Add((New-Record -SourceUrl $url -Status 'skipped' -Error 'invalid-or-non-http-url'))
        continue
    }

    $urlHost = $uri.Host.ToLowerInvariant()
    if ($urlHost -eq 'linkedin.com' -or $urlHost.EndsWith('.linkedin.com') -or
        $urlHost -eq 'joinhandshake.com' -or $urlHost.EndsWith('.joinhandshake.com')) {
        [void]$records.Add((New-Record -SourceUrl $url -Status 'skipped' -Error 'sign-in-only-source'))
        continue
    }

    $builder = [System.UriBuilder]::new($uri)
    $builder.Fragment = ''
    $normalizedUrl = $builder.Uri.AbsoluteUri
    if (-not $seenUrls.Add($normalizedUrl)) {
        continue
    }

    try {
        $response = Invoke-WebRequest -Uri $normalizedUrl -UseBasicParsing -TimeoutSec $TimeoutSeconds -Headers @{ 'User-Agent' = 'JobSearchJDPreprocessor/1.0' }
        $content = ConvertFrom-JobHtml -Html ([string]$response.Content)
        if ([string]::IsNullOrWhiteSpace($content.Text)) {
            throw 'No visible text found in public response.'
        }
        $contentHash = Get-Sha256Hex -Text $content.Text
        if (-not $seenContentHashes.Add($contentHash)) {
            continue
        }
        [void]$records.Add((New-Record -SourceUrl $url -NormalizedUrl $normalizedUrl -Status 'success' -Error $null -Title $content.Title -Text $content.Text -ContentHash $contentHash -FetchedAt ([datetime]::UtcNow.ToString('o'))))
    } catch {
        $message = [regex]::Replace([string]$_.Exception.Message, '\s+', ' ').Trim()
        if ($message.Length -gt 200) {
            $message = $message.Substring(0, 200)
        }
        [void]$records.Add((New-Record -SourceUrl $url -NormalizedUrl $normalizedUrl -Status 'error' -Error $message))
    }
}

$outputDirectory = Split-Path -Parent $OutputPath
if ($outputDirectory) {
    New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
}

[pscustomobject]@{
    generatedAt = [datetime]::UtcNow.ToString('o')
    records = @($records)
} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $OutputPath -Encoding utf8
