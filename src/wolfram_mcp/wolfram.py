import json
import os
import shutil
import logging
from pathlib import Path
from typing import List, Optional, Any, Sequence, Tuple

from wolframclient.evaluation import WolframLanguageSession
from wolframclient.language import wl, wlexpr


logger = logging.getLogger("wolfram_mcp.wolfram")


class WolframEngine:
    def __init__(self, kernel_path: Optional[str] = None):
        """Initialize the engine and attempt to resolve a kernel path.

        Resolution precedence:
          1. Explicit `kernel_path` argument
          2. Environment variable `WOLFRAM_KERNEL_PATH`
          3. Auto-discovery heuristics (static candidate list + `shutil.which` lookups)

        The resolved path (or None) is logged at INFO level so tests / users always
        see what was chosen without relying on raw prints.
        """
        # 1 / 2: explicit or env
        resolved = kernel_path or os.environ.get("WOLFRAM_KERNEL_PATH")
        # 3: auto-discovery
        if resolved is None:
            resolved = self._auto_discover_kernel()
        self._kernel_path = resolved
        logger.info("Resolved Wolfram Kernel path: %s", resolved)
        self.session: Optional[WolframLanguageSession] = None

    @staticmethod
    def _candidate_paths() -> List[str]:  # pragma: no cover - simple list builder
        """Return common kernel locations & discovered executables (best-effort)."""
        paths: List[str] = []
        if os.name == "posix":  # macOS & Linux
            # macOS app bundles
            paths += [
                "/Applications/Wolfram.app/Contents/MacOS/WolframKernel",
                "/Applications/Wolfram Engine.app/Contents/MacOS/WolframKernel",
            ]
            # Common /usr/local style installs
            paths += [
                "/usr/local/Wolfram/Mathematica/Kernel/WolframKernel",
                "/usr/local/Wolfram/Engine/Kernel/WolframKernel",
                "/usr/bin/WolframKernel",
                "/opt/Wolfram/WolframKernel",
            ]
        else:  # Windows typical locations
            program_files = os.environ.get("ProgramFiles", r"C:\\Program Files")
            program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\\Program Files (x86)")
            paths += [
                f"{program_files}\\Wolfram Research\\Mathematica\\13.3\\WolframKernel.exe",
                f"{program_files_x86}\\Wolfram Research\\Mathematica\\13.3\\WolframKernel.exe",
            ]
        # Add shutil.which discoveries (wolframscript resolves to kernel internally, but accept direct kernel too)
        for exe in ["WolframKernel", "wolframscript"]:
            found = shutil.which(exe)
            if found:
                paths.append(found)
        # Deduplicate preserving order
        seen = set()
        uniq: List[str] = []
        for p in paths:
            if p not in seen:
                uniq.append(p)
                seen.add(p)
        return uniq

    def _auto_discover_kernel(self) -> Optional[str]:  # pragma: no cover - heuristic
        for p in self._candidate_paths():
            if os.path.exists(p) and os.access(p, os.X_OK):
                logger.debug("Auto-discovered kernel candidate: %s", p)
                return p
        logger.debug("No auto-discovered Wolfram Kernel candidates were executable")
        return None

    def _ensure_session(self):
        if self.session is None:
            try:
                if not self._kernel_path:
                    raise RuntimeError(
                        "No Wolfram Kernel path found. Set WOLFRAM_KERNEL_PATH or install the Wolfram Engine (see README)."
                    )
                if not (os.path.exists(self._kernel_path) and os.access(self._kernel_path, os.X_OK)):
                    raise RuntimeError(
                        f"Configured Wolfram Kernel path is not executable: {self._kernel_path}"
                    )
                self.session = WolframLanguageSession(self._kernel_path)
                logger.info("Started Wolfram Kernel session: %s", self._kernel_path)
            except Exception as e:  # noqa: BLE001
                raise RuntimeError(
                    "Unable to start Wolfram Kernel. Provide a valid path via WOLFRAM_KERNEL_PATH or --kernel-path. Original error: " + str(e)
                ) from e

    def _eval_expr(self, expr: Any) -> Any:
        try:
            self._ensure_session()
            return self.session.evaluate(expr)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"Wolfram evaluation failed: {e}") from e

    def evaluate(self, code: str) -> str:
        res = self._eval_expr(wlexpr(code))
        return str(res)

    def evaluate_raster(self, code: str, fmt: str = "PNG", background: str | None = None, width: Optional[int] = None, height: Optional[int] = None, dpi: int | None = None) -> str:
        """Evaluate a Wolfram Language expression and rasterize the result to an image.

        Returns a JSON string with keys: format, width, height, data (base64), exprType, success, error(optional).

        Parameters:
          code: Wolfram Language source text (string form) to evaluate.
          fmt: Image export format (e.g. "PNG", "JPEG").
          background: Optional background color (e.g. "White").
          width/height: Optional explicit pixel dimensions (passed to Rasterize via ImageSize -> {w,h}).
          dpi: Optional raster resolution (ImageResolution option).
        """
        # Build WL code that safely attempts evaluation then rasterization, capturing errors.
        # Use ToExpression with HoldComplete to avoid premature evaluation of unintended input; here we deliberately evaluate.
        opts_parts: list[str] = []
        if background:
            # Background must be a WL color spec; we quote raw string input
            bg_lit = background if background.startswith("{") else background
            opts_parts.append(f"Background -> {bg_lit}")
        if dpi is not None:
            opts_parts.append(f"ImageResolution -> {int(dpi)}")
        size_part = ""
        if width is not None or height is not None:
            # If only one provided, let Mathematica infer the other proportionally by using Automatic.
            w = "Automatic" if width is None else str(int(width))
            h = "Automatic" if height is None else str(int(height))
            size_part = f"ImageSize -> {{{w},{h}}}"
            opts_parts.append(size_part)
        opts = ",".join(opts_parts)
        if opts:
            opts = "," + opts
        # We embed code literal as string then ToExpression; escape properly via json.dumps.
        code_lit = json.dumps(code)
        fmt_lit = json.dumps(fmt)
        # Rasterize to an Image; format is only applied at export step. Use BaseEncode for base64.
        # Use ToExpression with HoldComplete wrapper to catch syntax issues cleanly.
        # We pass the string, no custom context, and then release the HoldComplete to evaluate.
        wl_code = "".join([
            "Module[{expr,img,ok=True,err=Null,msgs={},dims,data,bytes,held},",
            "Block[{$MessageList},",
            f"held = Quiet@Check[ToExpression[{code_lit}, StandardForm, HoldComplete], (ok=False; err=\"EvaluationFailed\"; $Failed)];",
            "msgs = $MessageList;",
            "];",
            "If[held === $Failed, expr = $Failed, expr = ReleaseHold[held]];",
            "If[ok && expr === $Failed, ok=False; err=\"EvaluationFailed\"];",
            "If[ok && expr =!= $Failed,",
            f"img = Quiet@Check[Rasterize[expr{opts}], (ok=False; err=\"RasterizeFailed\"; $Failed)],",
            "img = $Failed];",
            "If[img === $Failed && ok, ok=False; If[err===Null, err=\"RasterizeFailed\"]];",
            "If[img =!= $Failed, dims = ImageDimensions[img], dims = {Null, Null}];",
            f"bytes = If[img === $Failed, Null, ExportByteArray[img, {fmt_lit}]];",
            "data = If[bytes === Null, Null, BaseEncode[bytes]];",
            "ExportString[<|",
            f"\"format\" -> {fmt_lit},",
            "\"success\" -> ok && img =!= $Failed,",
            "\"exprType\" -> If[expr === $Failed, \"$Failed\", ToString[Head[expr]]],",
            "\"error\" -> If[ok && img =!= $Failed, Null, err],",
            "\"messages\" -> ToString[msgs],",
            "\"width\" -> dims[[1]],",
            "\"height\" -> dims[[2]],",
            "\"data\" -> data",
            "|>, \"JSON\"]",
            "]"
        ])
        # Debug log (could later be behind verbosity flag)
        # print("WL_CODE:", wl_code)
        return self.evaluate(wl_code)

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
