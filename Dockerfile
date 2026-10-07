# syntax=docker/dockerfile:1

############################
# develop stage
############################
FROM python:3.14.3-slim-bookworm AS develop

WORKDIR /django

# .pyc を無駄に書かせない
ENV PYTHONDONTWRITEBYTECODE=1 \
    # ログを stdout/stderr に即時出す
    PYTHONUNBUFFERED=1 \
    # pip のバージョン確認はスキップ
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    # pip キャッシュを残さずイメージを太らせない
    PIP_NO_CACHE_DIR=1

# - 開発環境でも mysqlclient など C 拡張のビルドが必要になりやすい
# - pdf2image のために poppler-utils が必要
# - git/curl は開発コンテナ内で調査などで念のため追加
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
    build-essential \
    default-libmysqlclient-dev \
    pkg-config \
    poppler-utils \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# - requirements.txt を先にコピーすると依存レイヤーのキャッシュが効きやすい
COPY requirements.txt /django/requirements.txt

RUN pip install --upgrade pip \
    && pip install -r /django/requirements.txt

# - 開発用ステージは manage.py runserver を 8000 番で待ち受ける前提
# - bind mount でソースを上書きする運用でも、イメージ単体で最低限動くようにしておく
COPY . /django/

EXPOSE 8000

# - 開発では runserver を使う
# - 0.0.0.0 で待ち受けないとコンテナ外からアクセスできない
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]


############################
# builder stage
############################
FROM python:3.14.3-slim-bookworm AS builder

WORKDIR /django

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    PIP_NO_CACHE_DIR=1

# - builder は本番用の Python パッケージを wheel 化する専用
# - 本番では不要な build tool を builder に閉じ込める
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
    build-essential \
    default-libmysqlclient-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /django/requirements.txt

# - wheel を作っておくと production 側で再コンパイルせずに入れられる
# - builder と production を同系統イメージに揃えることで事故を減らす
RUN pip install --upgrade pip \
    && pip wheel --wheel-dir /wheels -r /django/requirements.txt


############################
# production stage
############################
FROM python:3.14.3-slim-bookworm AS production

WORKDIR /django

ARG APP_UID=1000
ARG APP_GID=1000

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    PIP_NO_CACHE_DIR=1

# - production には実行に必要なランタイムだけを入れる
# - libmariadb3 は mysqlclient 実行時のランタイムとして使う想定
# - poppler-utils は pdf2image のために必要
# - build-essential などは本番には不要
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
    libmariadb3 \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /django/requirements.txt
COPY --from=builder /wheels /wheels

# - builder で作成した wheel からインストールする
# - /usr/lib などを雑に丸コピーしない
RUN pip install --upgrade pip \
    && pip install /wheels/*

COPY . /django/
COPY entrypoint.prod.sh /django/entrypoint.prod.sh

# - root のまま動かさず、専用ユーザー zoomail で実行する
# - /django/collected_static と /django/private_media は本番用Composeで
#   ホスト側ディレクトリを bind mount する想定
# - collectstatic / アップロード保存はこの zoomail ユーザーで実行されるため、
#   ホスト側の collected_static/private_media も事前に Actions のビルド引数
#   APP_UID:APP_GID に揃えておく必要がある
# - bind mount 先の所有者が root:root などのままだと、コンテナ内でディレクトリ作成に失敗する
RUN addgroup --system --gid "${APP_GID}" zoomail \
    && adduser --system --uid "${APP_UID}" --ingroup zoomail zoomail \
    && mkdir -p /django/logs /django/collected_static /django/private_media \
    && chmod +x /django/entrypoint.prod.sh \
    && chown -R zoomail:zoomail /django

USER zoomail

EXPOSE 8000

# - nginx の後段で gunicorn を 8000 番待ち受けにする定番構成
# - ログはファイルではなく stdout/stderr に流す
# - workers/threads は初期値。CPU や負荷に応じて後で調整する
ENTRYPOINT ["/django/entrypoint.prod.sh"]
