<#
.SYNOPSIS
    Resolve the SSH target of the run server for the deploy scripts.

.DESCRIPTION
    Dot-source this file, then call Resolve-ServerTarget. The repository is public,
    so no tracked file names the run server: its address comes from the caller.

    Host: the -ServerHost argument, else the environment variable EQASIM_SERVER_HOST.
          It may be a host name, an IP address or a Host alias from ~/.ssh/config.
          There is no built-in default: if neither is set the function throws.
    User: the -ServerUser argument, else EQASIM_SERVER_USER. It is optional; without
          it the target is the bare host and ssh takes the user from ~/.ssh/config
          (or the local user name).

    Note for the rsync path of sync_data_to_server.ps1: rsync runs inside WSL, whose
    ssh reads the WSL ~/.ssh/config, not the Windows one. A Host alias must exist
    there as well, otherwise pass a resolvable host name.
#>

function Resolve-ServerTarget {
    param(
        [string]$ServerUser,
        [string]$ServerHost
    )

    if ([string]::IsNullOrWhiteSpace($ServerHost)) { $ServerHost = $env:EQASIM_SERVER_HOST }
    if ([string]::IsNullOrWhiteSpace($ServerUser)) { $ServerUser = $env:EQASIM_SERVER_USER }

    if ([string]::IsNullOrWhiteSpace($ServerHost)) {
        throw ("Run server not configured. Pass -ServerHost <host name, IP or ssh config alias> " +
               "or set the environment variable EQASIM_SERVER_HOST (optionally EQASIM_SERVER_USER). " +
               "There is no built-in default.")
    }

    $ServerHost = $ServerHost.Trim()
    if ([string]::IsNullOrWhiteSpace($ServerUser)) {
        $target = $ServerHost
    }
    else {
        $ServerUser = $ServerUser.Trim()
        $target = "${ServerUser}@${ServerHost}"
    }

    return [pscustomobject]@{
        User   = $ServerUser
        Host   = $ServerHost
        Target = $target
    }
}
