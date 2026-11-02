# Third-Party Notices

## Tailscale tsnet

The Windows release includes a helper executable built from the open-source
[`tailscale.com/tsnet`](https://github.com/tailscale/tailscale) Go module
(version v1.104.4), distributed under the BSD 3-Clause License. The helper is
used only for userspace connectivity to a user-supplied Headscale control
server. The release does not bundle a Tailscale account, auth key, or public
control-plane service.

## Headscale

`deploy/headscale` contains deployment examples for the open-source
[Headscale](https://github.com/juanfont/headscale) project, version 0.29.7.
Headscale itself is not bundled into the application; it must be deployed on
infrastructure controlled by the administrator.

## Caddy / Docker examples

The deployment examples reference the official Caddy and Headscale Docker
images. Those images are obtained and run by the administrator under their
respective licenses.

### Tailscale BSD 3-Clause License

```text
BSD 3-Clause License

Copyright (c) 2020 Tailscale Inc & contributors.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its contributors
   may be used to endorse or promote products derived from this software
   without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```
