' Her MCP autostart (hidden, no console window)
' ASCII-only on purpose: a .vbs/.cmd containing Chinese gets mangled on a GBK console.
' NOTE: "log" is a VBScript built-in (Log) -> use logfile.
' Python is auto-detected: DSH_PY -> PATH -> %USERPROFILE%\.dsh\dsh-runtimes

Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
script = dir & "\her-mcp-http.py"
logfile = dir & "\mcp.log"

py = sh.ExpandEnvironmentStrings("%DSH_PY%")
If py = "%DSH_PY%" Then py = ""
If py <> "" Then
  If Not fso.FileExists(py) Then py = ""
End If

If py = "" Then
  On Error Resume Next
  ' where python -> first line
  Set exec = sh.Exec("cmd /c where python")
  If Err.Number = 0 Then
    line = exec.StdOut.ReadLine()
    If Not IsNull(line) Then
      If line <> "" Then
        If fso.FileExists(line) Then py = line
      End If
    End If
  End If
  Err.Clear
  On Error GoTo 0
End If

If py = "" Then
  root = sh.ExpandEnvironmentStrings("%USERPROFILE%") & "\.dsh\dsh-runtimes"
  If fso.FolderExists(root) Then
    py = FindPython(fso, root)
  End If
End If

If py = "" Then
  ' give up silently: autostart should never pop a dialog
  WScript.Quit 1
End If

cmd = """" & py & """" & " """ & script & """ --port 8790 --log """ & logfile & """"
sh.Run cmd, 0, False

Function FindPython(fso, folder)
  FindPython = ""
  For Each child in fso.GetFolder(folder).SubFolders
    cand = child.Path & "\python.exe"
    If fso.FileExists(cand) Then
      FindPython = cand
      Exit Function
    End If
    found = FindPython(fso, child.Path)
    If found <> "" Then
      FindPython = found
      Exit Function
    End If
  Next
End Function
