# Приватный репозиторий

Решение загружено в https://github.com/Vimp17/contract-advance-report-20261115 через подключение GitHub. Репозиторий создан приватным через браузер: CLI в рабочей среде не имеет сетевого доступа вне песочницы.

История загрузки сохраняет порядок «тесты → реализация → документация»; перед ней есть служебный начальный коммит .gitignore, необходимый для инициализации через API. Исходные локальные хеши коммитов отличаются от серверных.

Клонирование:

```bash
gh auth login --hostname github.com
gh repo clone Vimp17/contract-advance-report-20261115
cd contract-advance-report-20261115
python3 -m unittest discover -s tests -v
python3 report.py --date 2026-11-10 --output-dir reports/2026-11-10
```

Для доступа проверяющему владелец должен добавить его GitHub-аккаунт в Settings → Collaborators. Приватная ссылка сама по себе доступа не даёт.

Далее — инструкции для восстановления исходной локальной истории из bundle или создания другой приватной копии.

## Загрузить с сохранением истории

Рядом с архивом исходников поставляется `contract-report.bundle`: это полный локальный Git-репозиторий, включая порядок «тесты → реализация → документация».

Установите Git и GitHub CLI, затем выполните в папке с bundle:

```bash
gh auth login --hostname github.com
git clone contract-report.bundle contract-report
cd contract-report
# origin пока указывает на локальный bundle; удаляем только эту настройку.
git remote remove origin
python3 -m unittest discover -s tests -v
gh repo create contract-advance-report-20261115 --private --source . --remote origin --push --description "Dated contractor advance report in Python"
gh repo view --json url,isPrivate
```

Последняя команда выдаст реальную ссылку и `isPrivate: true`. Если имя занято, выберите другое в `gh repo create`. Не используйте `--public`: исходное задание запрещает публикацию.

Для доступа проверяющему откройте Settings → Collaborators и добавьте его GitHub-аккаунт. Затем отправьте ссылку на репозиторий туда, откуда получили задание. Эта инструкция не рассылает приглашения автоматически.

## Если есть только ZIP

Распакуйте архив, перейдите в папку `contract-report` и выполните:

```bash
git init -b main
git add .
git commit -m "Add tested contractor payment report and documentation"
gh auth login --hostname github.com
gh repo create contract-advance-report-20261115 --private --source . --remote origin --push
gh repo view --json url,isPrivate
```

В этом варианте прежняя история коммитов не сохраняется. Отчёты о прогоне тестов приложены в `docs/tests-before.txt` и `docs/tests-after.txt`; они не являются полным транскриптом Claude Code. Отсутствие требуемого экспорта раскрыто в README.
