# GitHub Actionsでビルドする本番デプロイ

## 現在のActions構成

移行準備として、以下の2つを使います。

| Workflow | 起動方法 | 処理 |
| --- | --- | --- |
| `deploy-legacy.yaml` / Deploy to server | `master` へのpush | 従来のSSH接続 → VPSでgit pull → `compose.prod.yaml` でビルド・起動 |
| `deploy.yaml` / Build production image (manual) | `workflow_dispatch` のみ | Actionsで本番ビルド → Django設定チェック → GHCRへpush |

手動実行はActions画面で **Build production image (manual)** → **Run workflow** を選び、
ビルド対象のブランチを指定します。SSH部分はjob全体をコメントアウトしているため、VPSへ接続しません。

1. Dockerfileの `production` ステージを `linux/amd64` 向けにビルドする。
2. 本番イメージで `python manage.py check` を実行する（DB接続・全テストの実行は含まない）。
3. `ghcr.io/suzukake8/zoomail:sha-<commit SHA>` と `:production` をpushする。

手動ビルドは直列化します。選択したブランチのイメージで `:production` が更新されます。
現在の従来デプロイはGHCRイメージを使用しません。

## VPS移行後の構成

GHCR用Composeは `compose.registry.yaml` です。従来の `compose.prod.yaml` はVPSビルド用として維持します。
固定スクリプトを有効にすると、以下の処理を実行します。

1. SSHで `sudo -n /usr/local/sbin/deploy-zoomail` だけを実行する。
2. VPSがDjangoイメージだけをpullし、起動とHTTP応答を確認する。

固定スクリプトは `flock` で多重実行を防ぎます。
自動ロールバックは行いません。起動失敗時には失敗として報告します。
MariaDBは固定スクリプトから更新・再作成しません。

移行完了後、従来のworkflowを無効化し、`deploy.yaml` のSSH jobのコメントを外します。
`master` pushでの自動デプロイを新構成へ移す場合は、その時点で `deploy.yaml` にpushトリガーを追加します。
手動実行でSSHも動かす場合、コメント内の `if` により `master` 選択時だけVPSを更新します。

## CPUアーキテクチャ

本番VPSの `uname -m` は **`x86_64`** と確認済みです。
Dockerでのプラットフォーム名は **`linux/amd64`** なので、workflowの `platforms` はこの値に固定します。
Actionsの `ubuntu-latest` ランナーもx86_64を使うため、QEMUによるCPUエミュレーションは不要です。
`DEPLOY_PLATFORM` Variableの設定も不要です。

VPSで再確認する場合は、管理者が以下を実行します。

```bash
uname -m
sudo docker info --format '{{.Architecture}}'
```

`uname -m` の `x86_64` とDockerの `amd64` / `x86_64` は同じCPU系統を表します。
今後VPSを別のCPUへ移行する場合は、workflowのビルド先とランナーも合わせて見直してください。

## GitHubの設定

Repository Settings → Secrets and variables → Actionsで設定します。

| 種類 | 名前 | 設定 |
| --- | --- | --- |
| Variable | `APP_UID` | 保存ディレクトリの所有UID。省略時は1000 |
| Variable | `APP_GID` | 保存ディレクトリの所有GID。省略時は1000 |
| Secret | `SSH_HOST` | VPSのホスト名/IP |
| Secret | `SSH_USERNAME` | 現在は従来デプロイのユーザーを維持。新構成への切り替え時に `deploy` へ変更 |
| Secret | `SSH_KEY` | 現在は従来の鍵を維持。切り替え時にdeploy専用の秘密鍵へ変更 |
| Secret | `SSH_PORT` | SSHポート |
| Secret | `SSH_PASSPHRASE` | 秘密鍵のパスフレーズ（設定している場合） |
| Secret | `SSH_FINGERPRINT` | VPSのSSHホスト鍵のSHA256フィンガープリント |

イメージ名はリポジトリ名から小文字で生成します。forkではそのリポジトリ名になります。
Actionsのpushには自動発行の `GITHUB_TOKEN` と `packages: write` を使います。
手動ビルドにはSSH Secretsを使いません。従来デプロイが使用中のSSH Secretsは移行完了まで維持します。
本番DBパスワードなどのアプリ用SecretsはActionsに渡しません。

GHCRパッケージはprivateで運用します。既存パッケージがある場合は、このリポジトリの
Actionsにwrite権限があることを確認してください。
VPSのpullにはパッケージを読めるアカウントのPAT（classic、`read:packages`）を使います。
[GitHubのContainer registry認証ドキュメント](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)

## VPSの初回移行（管理者が実行）

**この移行を完了してから、新しいworkflowのSSH jobを有効にしてください。**
移行作業中は従来workflowによる自動更新を一時停止し、同時デプロイを防いでください。
以下は既存の配置 `/srv/zoomail` とデータパスを維持する手順です。
リポジトリのファイル編集だけではVPSの権限やGitHubのSecretsは更新されません。

### 1. 既存設定・データを確認する

```bash
uname -m
sudo stat -c '%u:%g %n' /srv/zoomail/collected_static /srv/zoomail/private_media
sudo docker compose -f /srv/zoomail/compose.prod.yaml ls
```

現在のComposeプロジェクト名が `zoomail` であることを確認します。
異なる場合は、`deploy-zoomail` の `--project-name` を既存名に変更してから配置してください。
別名で起動すると既存DBコンテナと競合します。
DBとprivate_mediaのバックアップを取得してから移行します。
`mariadb` のデータ、保存ディレクトリ、nginxの配信パスを移動・削除しないでください。

アプリのUID/GIDはSSHのdeployユーザーとは別の専用ユーザーにします。
現在のデータ所有者がSSHユーザーと同じ場合は、未使用のUID/GIDでアプリ用ユーザーを作り、
**collected_staticとprivate_mediaだけ**の所有者を変更します。
MariaDBディレクトリの所有者は変更しません。
専用のUID/GIDをActionsの `APP_UID` / `APP_GID` に設定してください。
nginxの静的ファイル読み取り権限も維持します。

### 2. SSHユーザーと固定ファイルを配置する

`deploy` が未作成なら、一般のsudo権限やDockerグループを付けずに作成します。

```bash
sudo adduser --disabled-password --gecos '' deploy
```

このリポジトリの更新済みファイルを管理者がVPSへ転送し、そのディレクトリから実行します。

```bash
sudo install -o root -g root -m 0755 deploy/deploy-zoomail /usr/local/sbin/deploy-zoomail
sudo install -o root -g root -m 0644 compose.registry.yaml /srv/zoomail/compose.registry.yaml
sudo chown root:root /srv/zoomail
sudo chmod 0755 /srv/zoomail
sudo chown root:root /srv/zoomail/.env
sudo chmod 0600 /srv/zoomail/.env
sudo visudo -cf deploy/zoomail-deploy.sudoers
sudo install -o root -g root -m 0440 deploy/zoomail-deploy.sudoers /etc/sudoers.d/zoomail-deploy
sudo visudo -c
```

`/srv`、`/usr/local/sbin`、`/etc/sudoers.d` もdeployユーザーから書き換えられないことを確認します。
Composeと.envは通常ファイルを使用し、deployユーザーが変更できるファイルへのsymlinkは使いません。
`/srv/zoomail` の既存ファイルにdeployユーザーの書き込み権限やACLが残っていないことも確認します。
アプリの保存ディレクトリはアプリユーザーが所有し、deployには書き込みを許可しません。
`chown -R root:root /srv/zoomail` はデータの所有者まで変えるため実行しません。

root所有の `/srv/zoomail/.env` に次を追加します。既存のDB・アプリ設定は維持します。

```dotenv
ZOOMAIL_IMAGE=ghcr.io/suzukake8/zoomail:production
```

`APP_UID` / `APP_GID` はActions側のビルド引数で決まります。本番.envで変更しても
イメージ内のユーザーは変わりません。

### 3. GHCRのpull認証をrootに設定する

rootのDocker設定にのみ保存します。トークンをdeployユーザーやActionsのSSHコマンドへ渡しません。

```bash
read -rp 'GitHub username: ' GHCR_USER
read -rsp 'GHCR read:packages token: ' GHCR_TOKEN
printf '\n'
printf '%s' "$GHCR_TOKEN" | sudo -H docker login ghcr.io --username "$GHCR_USER" --password-stdin
unset GHCR_TOKEN GHCR_USER
sudo chmod 0700 /root/.docker
sudo chmod 0600 /root/.docker/config.json
```

### 4. SSH鍵を固定コマンドに制限する

deploy専用の公開鍵を `/home/deploy/.ssh/authorized_keys` に配置します。
鍵の先頭に以下の制限を付けます（`ssh-ed25519 ...` は実際の公開鍵に置き換える）。

```text
restrict,command="sudo -n /usr/local/sbin/deploy-zoomail" ssh-ed25519 ...
```

`.ssh` と `authorized_keys` はroot所有にし、deploy本人にも編集させません。
ホームディレクトリもroot所有・deployから書き込み不可にして、`.ssh` 自体の置き換えを防ぎます。

```bash
sudo chown root:root /home/deploy
sudo chmod 0755 /home/deploy
sudo chown -R root:root /home/deploy/.ssh
sudo chmod 0755 /home/deploy/.ssh
sudo chmod 0644 /home/deploy/.ssh/authorized_keys
```

deployに汎用sudo権限、Dockerグループ、他のSSH鍵やパスワードログインを与えません。
管理用ubuntuユーザーは管理者用sudoを保持できますが、Dockerグループは不要です。
既存グループから外す場合は、再ログインするまで古いグループ権限が残ります。

### 5. 初回起動と確認

最初のイメージがGHCRにpushされた後、既存DBが稼働している状態で実行します。

```bash
sudo -n /usr/local/sbin/deploy-zoomail
sudo docker compose --project-name zoomail --env-file /srv/zoomail/.env \
  -f /srv/zoomail/compose.registry.yaml ps
```

固定スクリプトは `--no-deps` を使うため、停止中のDBは起動しません。
DBが停止している場合は管理者が `compose.registry.yaml` で `up -d database` を実行します。
DB更新やCompose変更も管理者が行います。
SSH jobを有効化した後にビルド・pushが成功してSSHのみ失敗した場合は、VPS設定を確認し、
Actionsを再実行してください。

`entrypoint.prod.sh` は `collectstatic`、`migrate` の完了後にgunicornを起動します。
HTTPヘルスチェックが成功しない場合、デプロイは失敗します。

## 手動で以前のイメージに戻す

管理者が `/srv/zoomail/.env` の `ZOOMAIL_IMAGE` を既知の
`ghcr.io/suzukake8/zoomail:sha-<以前のcommit SHA>` に変更し、固定スクリプトを実行します。
DBマイグレーションは自動では巻き戻りません。DBとの互換性を確認してください。
次の自動デプロイを受けるには `.env` を `:production` に戻します。

旧イメージは自動削除しません。容量管理は管理者が行います。
バックアップも管理者/rootが実行します。SSHのdeployユーザーにはバックアップ用Docker権限を追加しません。
