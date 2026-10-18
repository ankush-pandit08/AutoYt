param([string]$TextFile, [string]$OutputFile)
$ErrorActionPreference = 'Stop'
$voice = New-Object -ComObject SAPI.SpVoice
$english = @($voice.GetVoices() | Where-Object { $_.GetAttribute('Language') -match '(^|;)409(;|$)' })
if ($english.Count -eq 0) { throw 'Install an English US Windows speech voice or use Kokoro.' }
$voice.Voice = $english[0]
$voice.Rate = 0
$stream = New-Object -ComObject SAPI.SpFileStream
$stream.Format.Type = 22
try {
    $stream.Open($OutputFile, 3, $false)
    $voice.AudioOutputStream = $stream
    $null = $voice.Speak([System.IO.File]::ReadAllText($TextFile))
} finally {
    $stream.Close()
}
