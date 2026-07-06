# UPDATER1C_UPDATE_CHAIN_ZIP_SINGLE_PATCH_20260706
# ZIP-бэкап файловой базы + режим "установить только одно обновление"
# + последовательная цепочка обновлений по UpdInfo.txt / FromVersions.

from pathlib import Path
import re
import shutil
import zipfile
import tarfile


PATCH_MARKER = "UPDATER1C_UPDATE_CHAIN_ZIP_SINGLE_PATCH_20260706"


def install(ns):
    Gtk = ns.get("Gtk")
    Ui = ns.get("Ui")
    PathCls = ns.get("Path") or Path
    safe_name = ns.get("safe_name", lambda x: re.sub(r"[^A-Za-zА-Яа-я0-9_.-]+", "_", str(x or "")))
    now_stamp = ns.get("now_stamp", lambda: "now")
    normalize_base_kind_and_connect = ns.get("normalize_base_kind_and_connect")
    version_key = ns.get("version_key", lambda v: [int(x) for x in re.findall(r"\d+", str(v or ""))])
    is_valid_config_release = ns.get("is_valid_config_release", lambda v: bool(re.search(r"\d+\.\d+\.\d+", str(v or ""))))
    release_version_from_text = ns.get("release_version_from_text", lambda v: "")
    update_program_dir = ns.get("update_program_dir")
    find_update_file_in_dir = ns.get("find_update_file_in_dir")
    release_version_for_downloaded_folder = ns.get("release_version_for_downloaded_folder")
    unpack_update_archives_in_folder = ns.get("unpack_update_archives_in_folder")
    original_find_steps = ns.get("find_downloaded_update_steps")

    ns["UPDATER1C_UPDATE_SINGLE_STEP_MODE"] = True

    def _norm_version(value):
        return str(value or "").strip().replace("_", ".")

    def _same_version(a, b):
        return _norm_version(a) == _norm_version(b)

    def _read_text_any(path):
        path = Path(path)
        for enc in ("utf-8-sig", "utf-8", "cp1251", "cp866"):
            try:
                return path.read_text(encoding=enc, errors="replace")
            except Exception:
                pass
        return ""

    def _parse_updinfo_file(path):
        text = _read_text_any(path)
        result = {
            "version": "",
            "from_versions": [],
            "path": str(path),
        }

        for raw in text.splitlines():
            line = raw.strip()
            if not line or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip().lower()
            value = value.strip().strip('"').strip("'")

            if key == "version":
                result["version"] = _norm_version(value)

            elif key == "fromversions":
                vals = []
                for item in value.split(";"):
                    item = _norm_version(item)
                    if item:
                        vals.append(item)
                result["from_versions"] = vals

        return result

    def _step_release(step):
        try:
            return _norm_version(step[0])
        except Exception:
            return ""

    def _step_cfu(step):
        try:
            return Path(step[1])
        except Exception:
            return None

    def _step_folder(step):
        try:
            return Path(step[2])
        except Exception:
            cfu = _step_cfu(step)
            return cfu.parent if cfu else None

    def _find_updinfo_for_step(step):
        cfu = _step_cfu(step)
        folder = _step_folder(step)

        candidates = []

        if folder:
            candidates.extend([
                folder / "UpdInfo.txt",
                folder / "updinfo.txt",
                folder / "UPDINFO.TXT",
            ])

        if cfu:
            candidates.extend([
                cfu.parent / "UpdInfo.txt",
                cfu.parent / "updinfo.txt",
                cfu.parent / "UPDINFO.TXT",
            ])

        for candidate in candidates:
            try:
                if candidate.exists():
                    return _parse_updinfo_file(candidate)
            except Exception:
                pass

        roots = []
        if folder:
            roots.append(folder)
        if cfu:
            roots.append(cfu.parent)

        for root in roots:
            try:
                for item in root.rglob("*"):
                    if item.is_file() and item.name.lower() == "updinfo.txt":
                        return _parse_updinfo_file(item)
            except Exception:
                pass

        return {
            "version": _step_release(step),
            "from_versions": [],
            "path": "",
        }

    def _merge_steps(primary, extra):
        result = []
        used = set()

        for step in list(primary or []) + list(extra or []):
            try:
                rel = _step_release(step)
                cfu = _step_cfu(step)
                folder = _step_folder(step)
                key = (rel, str(cfu.resolve()) if cfu else str(folder))
            except Exception:
                continue

            if not rel or key in used:
                continue

            used.add(key)
            result.append(step)

        result.sort(key=lambda s: version_key(_step_release(s)))
        return result

    def _scan_all_downloaded_steps(settings, program, log_func=None):
        result = []
        used = set()

        if not update_program_dir or not find_update_file_in_dir:
            return result

        try:
            root = update_program_dir(settings, program)
        except Exception:
            return result

        root = Path(root)

        if not root.exists():
            return result

        def add_folder(folder):
            folder = Path(folder)

            try:
                if "_metadata" in folder.parts or "_archives" in folder.parts:
                    return
            except Exception:
                pass

            try:
                if unpack_update_archives_in_folder:
                    unpack_update_archives_in_folder(folder, log_func)
            except Exception:
                pass

            try:
                update_file = find_update_file_in_dir(folder)
            except Exception:
                update_file = None

            if not update_file:
                return

            rel = ""
            try:
                if release_version_for_downloaded_folder:
                    rel = release_version_for_downloaded_folder(Path(update_file).parent, "")
            except Exception:
                rel = ""

            if not rel:
                try:
                    if release_version_for_downloaded_folder:
                        rel = release_version_for_downloaded_folder(folder, "")
                except Exception:
                    rel = ""

            if not rel:
                try:
                    rel = release_version_from_text(str(folder))
                except Exception:
                    rel = ""

            rel = _norm_version(rel)

            if not rel or not is_valid_config_release(rel):
                return

            key = (rel, str(Path(update_file).resolve()))

            if key in used:
                return

            used.add(key)
            result.append((rel, Path(update_file), Path(update_file).parent))

        try:
            for folder in [root] + [p for p in root.rglob("*") if p.is_dir()]:
                add_folder(folder)
        except Exception:
            pass

        result.sort(key=lambda s: version_key(_step_release(s)))
        return result

    def _next_allowed_step(current_version, steps, used_releases=None, log_func=None):
        current_version = _norm_version(current_version)
        used_releases = set(used_releases or [])

        exact = []
        fallback = []

        for step in steps:
            rel = _step_release(step)

            if not rel or rel in used_releases:
                continue

            try:
                if current_version and version_key(rel) <= version_key(current_version):
                    continue
            except Exception:
                pass

            meta = _find_updinfo_for_step(step)
            from_versions = [_norm_version(x) for x in meta.get("from_versions") or [] if _norm_version(x)]

            if from_versions:
                if any(_same_version(current_version, x) for x in from_versions):
                    exact.append(step)
            else:
                fallback.append(step)

        if exact:
            exact.sort(key=lambda s: version_key(_step_release(s)))
            return exact[0]

        if fallback:
            fallback.sort(key=lambda s: version_key(_step_release(s)))
            return fallback[0]

        return None

    def find_downloaded_update_steps_patched(settings, program, current_version="", log_func=None):
        original_steps = []

        if original_find_steps:
            try:
                original_steps = original_find_steps(settings, program, current_version, log_func)
            except Exception as e:
                if log_func:
                    log_func(f"Штатный поиск обновлений не сработал: {type(e).__name__}: {e}")

        scanned_steps = _scan_all_downloaded_steps(settings, program, log_func)
        all_steps = _merge_steps(original_steps, scanned_steps)

        current_version = _norm_version(current_version)
        single_step = bool(ns.get("UPDATER1C_UPDATE_SINGLE_STEP_MODE", True))

        if not all_steps:
            return []

        if log_func:
            try:
                log_func(f"Режим установки: {'только один релиз' if single_step else 'последовательная цепочка до последнего доступного релиза'}")
                log_func(f"Текущий релиз базы: {current_version or '-'}")
                log_func("Доступные локальные релизы: " + ", ".join(_step_release(s) for s in all_steps))
            except Exception:
                pass

        first = _next_allowed_step(current_version, all_steps, log_func=log_func)

        if not first:
            if log_func:
                log_func("Не найден следующий допустимый релиз по UpdInfo.txt / FromVersions.")
            return []

        if single_step:
            if log_func:
                meta = _find_updinfo_for_step(first)
                if meta.get("from_versions"):
                    log_func(f"Выбран один шаг: {current_version or '-'} -> {_step_release(first)}; FromVersions={';'.join(meta.get('from_versions'))}")
                else:
                    log_func(f"Выбран один шаг: {current_version or '-'} -> {_step_release(first)}; UpdInfo.txt не найден, выбран ближайший релиз.")
            return [first]

        chain = []
        used = set()
        cur = current_version

        for _ in range(100):
            step = _next_allowed_step(cur, all_steps, used, log_func)
            if not step:
                break

            rel = _step_release(step)

            if not rel or rel in used:
                break

            chain.append(step)
            used.add(rel)

            if log_func:
                meta = _find_updinfo_for_step(step)
                if meta.get("from_versions"):
                    log_func(f"Шаг цепочки: {cur or '-'} -> {rel}; FromVersions={';'.join(meta.get('from_versions'))}")
                else:
                    log_func(f"Шаг цепочки: {cur or '-'} -> {rel}; UpdInfo.txt не найден, fallback по версии.")

            cur = rel

        return chain

    ns["find_downloaded_update_steps"] = find_downloaded_update_steps_patched

    DialogCls = ns.get("AutoUpdateDialog")

    if DialogCls and Gtk and Ui and not getattr(DialogCls, "_u1c_update_chain_zip_single_patched", False):
        old_init = DialogCls.__init__
        old_get_values = DialogCls.get_values

        def new_init(self, parent):
            old_init(self, parent)

            try:
                self.set_default_size(860, 500)
            except Exception:
                pass

            try:
                active = self.backup_type.get_active()
                self.backup_type.remove_all()
                self.backup_type.append_text("Авто: файловая=ZIP 1Cv8.1CD, серверная=.dt")
                self.backup_type.append_text("ZIP-архив 1Cv8.1CD; для серверной будет .dt")
                self.backup_type.append_text("Выгрузка .dt через конфигуратор")
                self.backup_type.set_active(active if active >= 0 else 0)
            except Exception:
                pass

            try:
                self.single_update = Ui.check("Установить только одно обновление", True)
                box = self.get_content_area()
                box.pack_start(self.single_update, False, False, 6)
                self.single_update.set_tooltip_text(
                    "Включено: ставится только ближайший допустимый релиз. "
                    "Выключено: строится цепочка по UpdInfo.txt / FromVersions до последнего доступного локального релиза."
                )
            except Exception:
                self.single_update = None

            try:
                self.show_all()
            except Exception:
                pass

        def new_get_values(self):
            vals = old_get_values(self)

            try:
                single_update = bool(self.single_update.get_active())
            except Exception:
                single_update = True

            vals["single_update"] = single_update
            ns["UPDATER1C_UPDATE_SINGLE_STEP_MODE"] = single_update

            return vals

        DialogCls.__init__ = new_init
        DialogCls.get_values = new_get_values
        DialogCls._u1c_update_chain_zip_single_patched = True

    TargetCls = None

    for obj in list(ns.values()):
        if isinstance(obj, type) and hasattr(obj, "make_file_1cd_backup_for_update") and hasattr(obj, "on_auto_update"):
            TargetCls = obj
            break

    if TargetCls and not getattr(TargetCls, "_u1c_update_chain_zip_backup_patched", False):
        old_restore_pre = getattr(TargetCls, "restore_pre_update_backup", None)

        def make_file_1cd_backup_for_update_zip(self, log, base: dict, release: str) -> Path:
            one_cd = self.file_base_1cd_path(base)
            backup_root = Path(self.settings.get("backup_dir") or self.settings.get("backups_dir") or "/mnt/DataStore/Updater1C/1c-backups").expanduser()
            out_dir = backup_root / safe_name(base.get("name") or "base")
            out_dir.mkdir(parents=True, exist_ok=True)

            dest = out_dir / f"{safe_name(base.get('name') or 'base')}_before_{safe_name(release)}_{now_stamp()}_1Cv8.1CD.zip"

            log("=== Архивирование файловой базы 1Cv8.1CD в ZIP ===")
            log(f"Источник: {one_cd}")
            log(f"Архив: {dest}")

            if dest.exists():
                dest.unlink()

            with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
                zf.write(one_cd, arcname="1Cv8.1CD")

            log(f"ZIP-архив создан: {dest}")
            return dest

        def restore_file_1cd_backup_for_update_zip(self, log, base: dict, backup_file: Path):
            one_cd = self.file_base_1cd_path(base)
            backup_file = Path(backup_file)

            if not backup_file.exists():
                raise RuntimeError(f"Архив отката не найден: {backup_file}")

            failed = one_cd.with_name(f"1Cv8.1CD.failed_{now_stamp()}")
            log("=== Откат файловой базы из архива ===")
            log(f"Архив: {backup_file}")
            log(f"Текущий файл базы будет сохранен как: {failed}")

            if one_cd.exists():
                shutil.move(str(one_cd), str(failed))

            one_cd.parent.mkdir(parents=True, exist_ok=True)

            low = backup_file.name.lower()

            if low.endswith(".zip"):
                with zipfile.ZipFile(backup_file, "r") as zf:
                    member = None

                    for name in zf.namelist():
                        if Path(name).name.lower() == "1cv8.1cd":
                            member = name
                            break

                    if not member:
                        raise RuntimeError("В ZIP не найден файл 1Cv8.1CD.")

                    with zf.open(member, "r") as src, open(one_cd, "wb") as dst:
                        shutil.copyfileobj(src, dst)

            elif low.endswith(".tar.gz") or low.endswith(".tgz"):
                with tarfile.open(backup_file, "r:gz") as tar:
                    member = None

                    for item in tar.getmembers():
                        if Path(item.name).name.lower() == "1cv8.1cd":
                            member = item
                            break

                    if not member:
                        raise RuntimeError("В TAR.GZ не найден файл 1Cv8.1CD.")

                    extracted = tar.extractfile(member)

                    if extracted is None:
                        raise RuntimeError("Не удалось прочитать 1Cv8.1CD из TAR.GZ.")

                    with extracted as src, open(one_cd, "wb") as dst:
                        shutil.copyfileobj(src, dst)

            else:
                raise RuntimeError(f"Неподдерживаемый архив файловой базы: {backup_file}")

            if not one_cd.exists():
                raise RuntimeError("После восстановления файл 1Cv8.1CD не появился.")

            log(f"Файловая база восстановлена: {one_cd}")

        def restore_pre_update_backup_patched(self, log, base: dict, backup_file, vals: dict):
            if not backup_file:
                log("Откат невозможен: резервная копия не создавалась.")
                return

            backup_file = Path(backup_file)
            low = backup_file.name.lower()

            if low.endswith(".zip") or low.endswith(".tar.gz") or low.endswith(".tgz"):
                return self.restore_file_1cd_backup_for_update(log, base, backup_file)

            if backup_file.suffix.lower() == ".dt" and old_restore_pre:
                return old_restore_pre(self, log, base, backup_file, vals)

            log(f"Автооткат для типа копии {backup_file} не выполняется автоматически.")

        TargetCls.make_file_1cd_backup_for_update = make_file_1cd_backup_for_update_zip
        TargetCls.restore_file_1cd_backup_for_update = restore_file_1cd_backup_for_update_zip
        TargetCls.restore_pre_update_backup = restore_pre_update_backup_patched
        TargetCls._u1c_update_chain_zip_backup_patched = True

    print("UPDATER1C_UPDATE_CHAIN_ZIP_SINGLE_PATCH: установлен")
