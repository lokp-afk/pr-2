import json
import os
import sys
import urllib.request
import urllib.error
from xml.etree import ElementTree as ET
from typing import Dict, List, Set

_NUGET_CACHE = {}
#  Загрузка конфигурации
def load_config(path: str = "config.json") -> dict:
    if not os.path.exists(path):
        print(f"Ошибка: файл '{path}' не найден.", file=sys.stderr)
        sys.exit(1)
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as e:
        print(f"Ошибка загрузки config.json: {e}", file=sys.stderr)
        sys.exit(1)

    for key in ["package_name", "repository", "mode", "filter_substring"]:
        if key not in cfg:
            print(f"Ошибка: отсутствует параметр '{key}'", file=sys.stderr)
            sys.exit(1)
    if cfg["mode"] not in ("test", "nuget"):
        print("mode должен быть 'test' или 'nuget'", file=sys.stderr)
        sys.exit(1)
    return cfg

#  Загрузка тестового репозитория
def load_test_repo(path: str) -> Dict[str, List[str]]:
    if not os.path.exists(path):
        print(f"Ошибка: тестовый репозиторий '{path}' не найден.", file=sys.stderr)
        sys.exit(1)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Нормализуем: все значения — списки строк
        return {str(k): [str(d) for d in (v if isinstance(v, list) else [])] for k, v in data.items()}
    except Exception as e:
        print(f"Ошибка загрузки тестового репозитория: {e}", file=sys.stderr)
        sys.exit(1)
#  Заглушки для NuGet (не используется в тесте)
def get_direct_deps_nuget(pkg: str) -> List[str]:

    # Кэширование: если уже загружали — сразу вернуть
    if pkg in _NUGET_CACHE:
        return _NUGET_CACHE[pkg]

    try:
        pkg_lower = pkg.lower()
        # Шаг 1: Получить список версий
        versions_url = f"https://api.nuget.org/v3-flatcontainer/{pkg_lower}/index.json"
        with urllib.request.urlopen(versions_url) as resp:
            versions_data = json.load(resp)
        versions = versions_data.get("versions", [])
        if not versions:
            _NUGET_CACHE[pkg] = []
            return []

        latest_version = versions[-1]

        # Шаг 2: Загрузить .nuspec
        nuspec_url = f"https://api.nuget.org/v3-flatcontainer/{pkg_lower}/{latest_version}/{pkg_lower}.nuspec"
        with urllib.request.urlopen(nuspec_url) as resp:
            nuspec_content = resp.read().decode("utf-8")

        # Шаг 3: Распарсить XML
        root = ET.fromstring(nuspec_content)
        ns = {"ns": "http://schemas.microsoft.com/packaging/2013/05/nuspec.xsd"}
        deps = set()
        for dep in root.findall(".//ns:dependency", ns):
            dep_id = dep.get("id")
            if dep_id:
                deps.add(dep_id)
        deps = list(deps)

        # Сохраняем в кэш
        _NUGET_CACHE[pkg] = deps
        return deps

    except urllib.error.HTTPError as e:
        if e.code == 404:
            print(f"Пакет '{pkg}' не найден в NuGet.", file=sys.stderr)
        else:
            print(f"HTTP ошибка для '{pkg}': {e}", file=sys.stderr)
        _NUGET_CACHE[pkg] = []
        return []
    except Exception as e:
        print(f"Ошибка при загрузке '{pkg}' из NuGet: {e}", file=sys.stderr)
        _NUGET_CACHE[pkg] = []
        return []


#  Основной обход DFS с обнаружением циклов
def dfs_build_graph(
    pkg: str,
    deps_func,
    filter_str: str,
    graph: Dict[str, List[str]],
    visited: Set[str],
    rec_stack: Set[str]
) -> bool:
    if pkg in rec_stack:
        print(f"Цикл обнаружен: {pkg}", file=sys.stderr)
        return True
    if pkg in visited:
        return False

    visited.add(pkg)
    rec_stack.add(pkg)

    raw_deps = deps_func(pkg)
    if filter_str == "":
        filtered = raw_deps
    else:
        filtered = [d for d in raw_deps if filter_str not in d]
    graph[pkg] = filtered  # ← КЛЮЧЕВОЕ: всегда добавляем, даже если []

    cycle = False
    for dep in filtered:
        if dfs_build_graph(dep, deps_func, filter_str, graph, visited, rec_stack):
            cycle = True

    rec_stack.remove(pkg)
    return cycle

#  Обратные зависимости
def get_reverse_deps(target: str, graph: Dict[str, List[str]]) -> Set[str]:
    # Строим обратный граф
    rev = {}
    for p, deps in graph.items():
        for d in deps:
            rev.setdefault(d, []).append(p)

    result = set()
    visited = set()

    def dfs_rev(node):
        if node in visited:
            return
        visited.add(node)
        for parent in rev.get(node, []):
            result.add(parent)
            dfs_rev(parent)

    dfs_rev(target)
    return result

#  Mermaid
def to_mermaid(graph: Dict[str, List[str]]) -> str:
    lines = ["graph TD"]
    for pkg, deps in graph.items():
        for dep in deps:
            # Экранируем идентификаторы
            p = pkg.replace("-", "_").replace(".", "_")
            d = dep.replace("-", "_").replace(".", "_")
            if p[0].isdigit():
                p = "P" + p
            if d[0].isdigit():
                d = "P" + d
            lines.append(f"    {p} --> {d}")
    return "\n".join(lines)

#  MAIN
def main():
    config = load_config()
    package = config["package_name"]
    mode = config["mode"]
    repo_path = config["repository"]
    filter_str = config["filter_substring"]

    # Этап 1
    print(" Этап 1: Параметры конфигурации")
    for k, v in config.items():
        print(f"{k}: {v}")
    print()

    # Выбор источника зависимостей
    if mode == "test":
        test_repo = load_test_repo(repo_path)
        def get_deps(p): return test_repo.get(p, [])
    else:
        def get_deps(p): return get_direct_deps_nuget(p)

    # Этап 2: прямые зависимости
    print(" Этап 2: Прямые зависимости ")
    direct = get_deps(package)
    for d in direct:
        print(d)
    if not direct:
        print("(нет)")
    print()

    # Этап 3: полный граф
    print(" Этап 3: Построение графа зависимостей ")
    graph = {}
    visited = set()
    rec_stack = set()
    has_cycle = dfs_build_graph(package, get_deps, filter_str, graph, visited, rec_stack)
    if not has_cycle:
        print("Циклов не обнаружено.")
    print("Граф построен.")
    print()

    # Отладка: покажем граф
    # print("DEBUG graph:", graph)

    # Этап 4: обратные зависимости
    print(" Этап 4: Обратные зависимости ")
    rev_deps = get_reverse_deps(package, graph)
    if rev_deps:
        for r in sorted(rev_deps):
            print(r)
    else:
        print("(нет)")
    print()
    # Этап 5: Mermaid
    print(" Этап 5: Визуализация (Mermaid) ")
    print(to_mermaid(graph))
    print()

if __name__ == "__main__":
    main()