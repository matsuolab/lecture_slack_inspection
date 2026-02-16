FROM public.ecr.aws/lambda/python:3.12

# requirements.txt をコピーしてインストール
COPY requirements.txt ${LAMBDA_TASK_ROOT}
RUN pip install -r requirements.txt

# ソースコードをコピー
# (CDKのexclude設定により、必要なディレクトリだけがここに渡されます)
COPY . ${LAMBDA_TASK_ROOT}

# CMDはCDK側で上書きするため、ここではダミーまたはデフォルトを指定
CMD ["app.handler"]