Set ws = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' Paths
appDir = fso.GetParentFolderName(WScript.ScriptFullName)
batFile = appDir & "\plotter.bat"
shortcutPath = ws.SpecialFolders("Desktop") & "\Plotter CTRL.lnk"

' Create shortcut
Set lnk = ws.CreateShortcut(shortcutPath)
lnk.TargetPath = batFile
lnk.WorkingDirectory = appDir
lnk.Description = "Pen Plotter Control"
lnk.IconLocation = appDir & "\plotter.ico,0"
lnk.WindowStyle = 1
lnk.Save

WScript.Echo "Shortcut created on desktop: Plotter CTRL"
