import json
import os
from pathlib import Path
from typing import List, Optional, Any, Sequence, Tuple

from wolframclient.evaluation import WolframLanguageSession
from wolframclient.language import wl, wlexpr


class WolframEngine:
    def __init__(self, kernel_path: Optional[str] = None):
        # Defer starting a kernel until first evaluation (speeds server startup and allows running without kernel until needed)
        self._kernel_path = kernel_path
        self.session: Optional[WolframLanguageSession] = None

    def _ensure_session(self):
        if self.session is None:
            self.session = WolframLanguageSession(self._kernel_path) if self._kernel_path else WolframLanguageSession()

    def _eval_expr(self, expr: Any) -> Any:
        try:
            self._ensure_session()
            return self.session.evaluate(expr)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"Wolfram evaluation failed: {e}") from e

    def evaluate(self, code: str) -> str:
        res = self._eval_expr(wlexpr(code))
        return str(res)

    # Notebook operations
    def create_notebook(self, path: str, cells: Optional[List[str]] = None) -> str:
        """Create a notebook optionally populated with Input-style cells.

        If no cells are provided, an empty notebook (no placeholder cell) is created.
        """
        styled: List[Tuple[str, str]] = [] if cells is None else [(c, "Input") for c in cells]
        return self.create_notebook_styled(path, styled)

    def create_notebook_styled(self, path: str, cells: Sequence[Tuple[str, str]] | None = None) -> str:
        """Create a notebook with (content, style) tuples.

        Each tuple: (content, style). Style defaults to Input if missing (caller can pre-normalize).
        """
        nb_path = Path(path)
        nb_path.parent.mkdir(parents=True, exist_ok=True)
        # If no cells provided, create an empty notebook (no default placeholder cell)
        if cells is None:
            cells = []
        wl_cells: List[Any] = []
        for txt, style in cells:
            style = style or "Input"
            # Do NOT evaluate/parse Input cells; insert literal source exactly as provided.
            # For all styles we now just store the raw text (frontend will allow manual execution later).
            wl_cells.append(wl.Cell(txt, style))
        expr = wl.Export(str(nb_path), wl.Notebook(wl_cells), "NB")
        self._eval_expr(expr)
        return str(nb_path)

    def append_cell(self, path: str, cell: str, style: str = "Input") -> str:
        return self.append_cells(path, [(cell, style)])

    def append_cells(self, path: str, cells: Sequence[Tuple[str, str]]) -> str:
        """Append multiple (content, style) cells. Returns new total cell count."""
        # Build WL code inserting cells literally without parsing/evaluating Input contents.
        contents = [json.dumps(c) for c, _ in cells]
        styles = [json.dumps((s or "Input")) for _, s in cells]
        wl_code = (
            "Module[{nb, existing, contents, styles, newCells}," +
            f"nb = Import[{json.dumps(path)}];" +
            "existing = First[nb];" +
            "contents = {" + ",".join(contents) + "};" +
            "styles = {" + ",".join(styles) + "};" +
            "newCells = MapThread[Cell, {contents, styles}];" +
            "existing = Join[existing, newCells];" +
            f"Export[{json.dumps(path)}, Notebook[existing], \"NB\"];" +
            "Length[existing]" +
            "]"
        )
        return self.evaluate(wl_code)

    def replace_cell(self, path: str, index: int, cell: str) -> str:
        code = (
            "Module[{nb=Import[" + json.dumps(path) + "], cells},"
            "cells=First[nb];"
            f"If[{index} < 1 || {index} > Length[cells], Return[\"IndexOutOfRange\"]];"
            f"cells[[{index}]] = Cell[BoxData[ToBoxes[{json.dumps(cell)}]], \"Input\"];"
            "nb2 = Notebook[cells]; Export[" + json.dumps(path) + ", nb2, \"NB\"];"
            f"ToString[cells[[{index}]]]"
            "]"
        )
        return self.evaluate(code)

    def list_cells(self, path: str) -> List[str]:
        # Prefer front-end path using NotebookOpen/Cells; fallback to Import.
        path_lit = json.dumps(path)
        wl_code = (
            "Module[{nb, cells, res, imported},"
            f"nb = Quiet@Check[NotebookOpen[{path_lit}, Visible->True], $Failed];"
            "If[nb === $Failed,"  # fallback branch
            f"imported = Import[{path_lit}]; cells = First[imported]; res = Map[ToString, cells];,"  # import path
            "cells = Cells[nb]; res = Map[Function[c, ToString[NotebookRead[c]]], cells];"  # front-end path
            "];"
            "StringRiffle[res, \"\\n---\\n\"]"
            "]"
        )
        output = self.evaluate(wl_code)
        return output.split("\n---\n") if output else []

    # --- Extended notebook utilities ---
    def get_cell(self, path: str, index: int) -> str:
        path_lit = json.dumps(path)
        wl_code = (
            "Module[{nb, cells, imported},"
            f"nb = Quiet@Check[NotebookOpen[{path_lit}, Visible->True], $Failed];"
            "If[nb === $Failed,"  # fallback
            f"imported = Import[{path_lit}]; cells = First[imported];,"  # import branch
            "cells = Cells[nb];"  # front-end branch
            "];"
            f"If[{index} < 1 || {index} > Length[cells], Return[\"IndexOutOfRange\"]];"
            f"ToString[If[Head[cells[[{index}]]] === CellObject, NotebookRead[cells[[{index}]]], cells[[{index}]]]] /. CellObject[__]:>\"<CellObject>\" // Quiet"
            "]"
        )
        return self.evaluate(wl_code)

    def search_notebook(self, path: str, query: str, ignore_case: bool = True, max_results: int = 50) -> str:
        """Search a notebook's cells for a substring.

        Returns a JSON string: {path, query, count, matches:[{index, content}]}.
        Large cell contents are truncated client-side if desired; this method returns full content.
        """
        ic_flag = "True" if ignore_case else "False"
        path_lit = json.dumps(path)
        query_lit = json.dumps(query)
        wl_code = (
            "Module[{nb, cells, q=" + query_lit + ", ic=" + ic_flag + ", max=" + str(max_results) + ", matches={}, cellObj, idx, imported},"
            f"nb = Quiet@Check[NotebookOpen[{path_lit}, Visible->True], $Failed];"
            "If[nb === $Failed,"  # fallback import scan
            # Use numbered formatting placeholders to avoid brace conflicts in WL code
            "imported = Import[{PL}]; cells = First[imported]; Do[With[{tx = ToString[cells[[i]]]}, If[StringContainsQ[tx, q, IgnoreCase->ic], AppendTo[matches, <|\"index\"->i, \"content\"->tx|>]]], {i, Length[cells]}];,"  # WL Do loop
            # front-end search path
            "cells = Cells[nb]; SelectionMove[nb, Before, Notebook];"
            "While[(max==0 || Length[matches]<max) && NotebookFind[nb, q, All, CellContents, IgnoreCase->ic] =!= $Failed,"
            "cellObj = NotebookSelection[nb];"
            "idx = Quiet@First@FirstPosition[cells, cellObj, Missing[\"NotFound\"]];"
            "If[idx =!= Missing[\"NotFound\"], AppendTo[matches, <|\"index\"->idx, \"content\"->ToString[NotebookRead[cellObj]]|>]];"
            "SelectionMove[nb, After, Cell];"
            "];"
            ";"  # end If
            "If[max>0 && Length[matches]>max, matches = Take[matches, max]];"
            f"ExportString[<|\"path\"->{path_lit}, \"query\"->q, \"count\"->Length[matches], \"matches\"->matches|>, \"JSON\"]"
            "]"
        )
        wl_code = wl_code.replace("{PL}", path_lit)
        return self.evaluate(wl_code)

    def export_notebook(self, path: str, format: str = "Plaintext") -> str:
        code = f"ExportString[Import[{json.dumps(path)}], {json.dumps(format)}]"
        return self.evaluate(code)

    # Documentation Page Generation (for resource functions & paclets)
    def create_function_documentation(self, symbol: str, out_dir: str) -> str:
        """Generate a simple documentation notebook for a given symbol.

        This is a minimal placeholder; real implementation would integrate with
        DocumentationTools / PacletManager and resource definition metadata.
        """
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        nb_path = out / f"{symbol}.nb"
        code_cells = [
            f"\"Documentation for {symbol}\"",
            f"?{symbol}",
            f"Information[{symbol}, LongForm -> True]",
            f"Definition[{symbol}]"
        ]
        self.create_notebook(str(nb_path), code_cells)
        return str(nb_path)

    def create_paclet_doc_skeleton(self, paclet_name: str, out_dir: str) -> str:
        base = Path(out_dir) / paclet_name / "Documentation" / "English"
        (base / "ReferencePages" / "Symbols").mkdir(parents=True, exist_ok=True)
        (base / "Guides").mkdir(parents=True, exist_ok=True)
        guide_nb = base / "Guides" / f"{paclet_name}Guide.nb"
        if not guide_nb.exists():
            self.create_notebook(str(guide_nb), [f"\"Guide for {paclet_name}\""])
        return str(base)

    def frontend_notebook_example(self, path: str) -> str:
        """Create & edit a notebook via front-end functions (NotebookWrite, SelectionMove).

        Returns a JSON string with keys: path, cells (stringified Cell expressions).
        Requires an available Wolfram Front End; will fail in pure headless kernel environments.
        """
        wl_code = (
            "Module[{nb,res,cells},"
            "nb=CreateDocument[Notebook[{}],Visible->False];"
            "NotebookWrite[nb, Cell[\"Example Notebook\",\"Title\"]];"
            "NotebookWrite[nb, Cell[\"Preface\",\"Text\"]];"
            "NotebookWrite[nb, Cell[BoxData@ToBoxes[2+2],\"Input\"]];"
            "NotebookWrite[nb, Cell[BoxData@ToBoxes[Plot[Sin[x],{x,0,6.28}]],\"Input\"]];"
            "SelectionMove[nb, Before, Notebook];"
            "NotebookWrite[nb, Cell[\"Inserted at Top\",\"Text\"]];"
            "res=NotebookGet[nb];"
            f"Export[{json.dumps(path)}, res, \"NB\"];"
            "cells=Map[ToString, First[res]];"
            "NotebookClose[nb];"
            f"ExportString[<|\"path\"->{json.dumps(path)},\"cells\"->cells|>,\"JSON\"]"
            "]"
        )
        return self.evaluate(wl_code)

    def close(self):  # pragma: no cover - cleanup helper
        try:
            if self.session is not None:
                self.session.terminate()
        except Exception:  # noqa: BLE001
            pass
