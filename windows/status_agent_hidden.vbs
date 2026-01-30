If WScript.Arguments.Count = 0 Then
  WScript.Echo "Usage: status_agent_hidden.vbs <powershell args>"
  WScript.Quit 1
End If

Dim shell
Dim args
Dim i

Set shell = CreateObject("WScript.Shell")
args = ""

For i = 0 To WScript.Arguments.Count - 1
  If i > 0 Then
    args = args & " "
  End If
  args = args & """" & WScript.Arguments(i) & """"
Next

shell.Run "powershell.exe " & args, 0, False
