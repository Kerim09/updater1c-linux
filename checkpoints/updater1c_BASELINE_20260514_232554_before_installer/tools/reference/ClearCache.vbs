'==========================================================================
'
' VBScript Source File -- Created with SAPIEN Technologies PrimalScript 2011
'
' NAME: ClearCache v2.1 Windows XP, Vista and 7 compatible
'
' AUTHOR: asv@asved.ru
' DATE  : 07.10.2014
'
' COMMENT: clears all files except *.lic of local cache, 
' clears "cache*" files at roaming profile of 1C
'
'==========================================================================

Option Explicit
Dim FolderPath 
Dim objFSO, objFolder, result
MsgBox "Пожалуйста, закройте все окна 1С и нажмите ОК и ожидайте следующее окно после очистки временных файлов",,"Очистка кэша"
' подождем завершения процессов
WScript.Sleep 5000
' clear local cache
FolderPath = LocalCachePath()
result = ClearPath(FolderPath)
' clear cache at roaming appdata
FolderPath = RoamingCachePath
Set objFSO = CreateObject("Scripting.FileSystemObject")
Set objFolder = objFSO.GetFolder(FolderPath)
result = result&Clear1CD(objFolder)
If result <> "" Then
	MsgBox "Ошибка удаления файлов: "&vbCrLf&result,,"ОШИБКА!"
Else
MsgBox "Очистка кэша выполнена.",, "УСПЕШНО."
End If
 
Function Clear1CD(objFolder)
On Error Resume Next 
Dim objFSO, colFolders, objFile, colFiles, filepath
Clear1CD = ""
Set objFSO = CreateObject("Scripting.FileSystemObject")
Set colFolders = objFolder.SubFolders
For Each objFile in colFolders
	Clear1CD = Clear1CD&Clear1CD(objFile)
Next
Set colFiles = objFolder.Files
For Each objFile in colFiles
	If Left(objFile.Name, 5) = "cache" Then
		filepath = objFile.Path
		objFSO.DeleteFile objFile, True
		If objFSO.FileExists(Filepath) Then
			Clear1CD = Clear1CD&filepath&vbCrLf
		End If
	End If
Next
End Function

Function ClearPath(FolderPath)
On Error Resume Next
Dim objFSO
Dim objFolder
Dim colFiles
Dim objFile, filepath
ClearPath = ""
Set objFSO = CreateObject("Scripting.FileSystemObject")
Set objFolder = objFSO.GetFolder(FolderPath)
Set colFiles = objFolder.SubFolders
For Each objFile in colFiles
	If objFile.Name <> "licenses" Then
		ClearPath = ClearPath&ClearFolder(objFile)
	End If
Next
End Function

Function ClearFolder(objFolder)
On Error Resume Next 
Dim objFSO, colFolders, objFile, colFiles, filepath
ClearFolder = ""
Set objFSO = CreateObject("Scripting.FileSystemObject")
Set colFolders = objFolder.SubFolders
For Each objFile in colFolders
	ClearFolder = ClearFolder&ClearFolder(objFile)
Next
Set colFiles = objFolder.Files
For Each objFile in colFiles
	If Right(objFile.Name, 3) <> "lic" Then
		filepath = objFile.Path
		objFSO.DeleteFile objFile, True
		If objFSO.FileExists(Filepath) Then
			ClearFolder = ClearFolder&filepath&vbCrLf
		End If
	End If
Next
End Function

Function LocalCachePath()
Dim Shell, ProfilePath
Set Shell = CreateObject("WScript.Shell")
ProfilePath = Shell.ExpandEnvironmentStrings("%LOCALAPPDATA%")
If ProfilePath = "%LOCALAPPDATA%" Then
	ProfilePath = Shell.ExpandEnvironmentStrings("%USERPROFILE%")+"\Local Settings\Application Data"
End If
LocalCachePath = ProfilePath+"\1C"
End Function

Function RoamingCachePath()
Dim Shell, ProfilePath
Set Shell = CreateObject("WScript.Shell")
ProfilePath = Shell.ExpandEnvironmentStrings("%APPDATA%")
RoamingCachePath = ProfilePath+"\1C"
End Function
