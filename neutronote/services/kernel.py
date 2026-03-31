"""
Persistent Python kernel for code cell execution.

Provides a shared kernel that maintains state across code executions,
allowing users to load workspaces and work with them collaboratively.
"""

import json
import os
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import psutil


@dataclass
class ExecutionResult:
    """Result of executing code in the kernel."""

    success: bool
    output: str
    error: Optional[str] = None
    execution_time: float = 0.0


@dataclass
class KernelStatus:
    """Current status of the kernel."""

    state: str  # 'starting', 'idle', 'busy', 'dead'
    pid: Optional[int] = None
    uptime_seconds: float = 0.0
    executions_count: int = 0
    last_execution_time: Optional[float] = None

    def to_dict(self):
        return {
            "state": self.state,
            "pid": self.pid,
            "uptime_seconds": self.uptime_seconds,
            "executions_count": self.executions_count,
            "last_execution_time": self.last_execution_time,
        }


@dataclass
class MemoryInfo:
    """Memory usage information."""

    system_total_gb: float
    system_used_gb: float
    system_percent: float
    mantid_used_gb: float = 0.0
    mantid_percent: float = 0.0  # Percent of system total
    warning: bool = False  # True if usage > 85%
    critical: bool = False  # True if usage > 95%

    def to_dict(self):
        return {
            "system_total_gb": round(self.system_total_gb, 2),
            "system_used_gb": round(self.system_used_gb, 2),
            "system_percent": round(self.system_percent, 1),
            "mantid_used_gb": round(self.mantid_used_gb, 2),
            "mantid_percent": round(self.mantid_percent, 1),
            "warning": self.warning,
            "critical": self.critical,
        }


@dataclass
class WorkspaceInfo:
    """Information about a Mantid workspace."""

    name: str
    ws_type: str
    num_spectra: int = 0
    num_bins: int = 0
    memory_mb: float = 0.0

    def to_dict(self):
        return {
            "name": self.name,
            "type": self.ws_type,
            "num_spectra": self.num_spectra,
            "num_bins": self.num_bins,
            "memory_mb": round(self.memory_mb, 2),
        }


class KernelManager:
    """
    Manages a persistent Python kernel process.

    The kernel runs as a subprocess and maintains state across executions.
    Communication happens via stdin/stdout with JSON messages.
    """

    # Singleton instance
    _instance: Optional["KernelManager"] = None
    _lock = threading.Lock()

    def __new__(cls):
        """Ensure only one KernelManager exists (singleton)."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._initialized = True
        self._process: Optional[subprocess.Popen] = None
        self._state = "dead"
        self._start_time: Optional[float] = None
        self._executions_count = 0
        self._last_execution_time: Optional[float] = None
        self._exec_lock = threading.Lock()

        # Start the kernel
        self.start()

    def start(self) -> bool:
        """Start the kernel process."""
        if self._process is not None and self._process.poll() is None:
            return True  # Already running

        self._state = "starting"

        # The kernel runner script
        kernel_script = self._get_kernel_script()

        try:
            self._process = subprocess.Popen(
                [sys.executable, "-c", kernel_script],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,  # Line buffered
            )
            self._start_time = time.time()
            self._state = "idle"
            self._executions_count = 0
            print(f"[KernelManager] Kernel started with PID {self._process.pid}")
            return True
        except Exception as e:
            print(f"[KernelManager] Failed to start kernel: {e}")
            self._state = "dead"
            return False

    def _get_kernel_script(self) -> str:
        """Return the Python code that runs in the kernel subprocess."""
        return '''
import sys
import json
import io
import traceback

# Try to import mantid - it's optional but desired
try:
    from mantid.simpleapi import *
    from mantid.api import AnalysisDataService as ADS
    MANTID_AVAILABLE = True
except ImportError:
    MANTID_AVAILABLE = False
    ADS = None

# Global namespace for user code
_user_namespace = {'__name__': '__main__'}

# Snapshot of namespace keys before any user code runs.
# Populated after imports so star-imports (e.g. mantid.simpleapi)
# are excluded from the variables pane.
_baseline_keys = set(_user_namespace.keys())

def get_workspace_info():
    """Get info about all workspaces in ADS."""
    if not MANTID_AVAILABLE or ADS is None:
        return []
    
    workspaces = []
    try:
        for name in ADS.getObjectNames():
            try:
                ws = ADS.retrieve(name)
                info = {
                    'name': name,
                    'type': type(ws).__name__,
                    'num_spectra': 0,
                    'num_bins': 0,
                    'memory_mb': 0.0,
                }
                
                # Try to get dimensions
                if hasattr(ws, 'getNumberHistograms'):
                    info['num_spectra'] = ws.getNumberHistograms()
                if hasattr(ws, 'blocksize'):
                    info['num_bins'] = ws.blocksize()
                if hasattr(ws, 'getMemorySize'):
                    info['memory_mb'] = ws.getMemorySize() / (1024 * 1024)
                
                workspaces.append(info)
            except Exception:
                pass
    except Exception:
        pass
    
    return workspaces

def get_mantid_memory_mb():
    """Get total memory used by Mantid workspaces."""
    if not MANTID_AVAILABLE or ADS is None:
        return 0.0
    
    total = 0.0
    try:
        for name in ADS.getObjectNames():
            try:
                ws = ADS.retrieve(name)
                if hasattr(ws, 'getMemorySize'):
                    total += ws.getMemorySize() / (1024 * 1024)
            except Exception:
                pass
    except Exception:
        pass
    
    return total

def get_namespace_vars():
    """Get list of user-defined variables in the namespace.
    
    Filters out:
      - private names (starting with _)
      - names that were in the namespace before any user code ran
      - callable objects originating from mantid (algorithm wrappers
        injected by ``from mantid.simpleapi import *``)
      - modules
    """
    import types as _types
    vars_list = []
    for name, val in _user_namespace.items():
        if name.startswith('_'):
            continue
        if name in _baseline_keys:
            continue
        # Skip modules
        if isinstance(val, _types.ModuleType):
            continue
        # Skip mantid algorithm wrappers (callable + mantid module origin)
        if callable(val):
            mod = getattr(val, '__module__', '') or ''
            if 'mantid' in mod:
                continue
        vars_list.append({
            'name': name,
            'type': type(val).__name__,
        })
    return vars_list

def execute_code(code):
    """Execute code and return result."""
    stdout_capture = io.StringIO()
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    
    try:
        sys.stdout = stdout_capture
        sys.stderr = stdout_capture
        
        exec(compile(code, '<neutronote>', 'exec'), _user_namespace)
        
        return {
            'success': True,
            'output': stdout_capture.getvalue(),
            'error': None,
        }
    except Exception as e:
        return {
            'success': False,
            'output': stdout_capture.getvalue(),
            'error': traceback.format_exc(),
        }
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr

# -----------------------------------------------------------------
# Workspace interactivity helpers
# -----------------------------------------------------------------

def rename_workspace(old_name, new_name):
    """Rename a workspace in the ADS."""
    if not MANTID_AVAILABLE or ADS is None:
        return {'success': False, 'error': 'Mantid not available'}
    if not ADS.doesExist(old_name):
        return {'success': False, 'error': f'Workspace "{old_name}" not found'}
    try:
        RenameWorkspace(InputWorkspace=old_name, OutputWorkspace=new_name)
        return {'success': True, 'old_name': old_name, 'new_name': new_name}
    except Exception as e:
        return {'success': False, 'error': str(e)}

def get_algorithm_history(ws_name):
    """Get the algorithm history of a workspace."""
    if not MANTID_AVAILABLE or ADS is None:
        return {'success': False, 'error': 'Mantid not available'}
    if not ADS.doesExist(ws_name):
        return {'success': False, 'error': f'Workspace "{ws_name}" not found'}
    try:
        ws = ADS.retrieve(ws_name)
        history = ws.getHistory()
        items = []
        for i in range(history.size()):
            alg_hist = history.getAlgorithmHistory(i)
            props = []
            for p in range(alg_hist.numberOfProperties()):
                prop = alg_hist.getPropertyHistory(p)
                if not prop.isDefault():
                    props.append({'name': prop.name(), 'value': prop.value()})
            items.append({
                'name': alg_hist.name(),
                'version': alg_hist.version(),
                'execution_date': str(alg_hist.executionDate()),
                'duration': alg_hist.executionDuration(),
                'properties': props,
            })
        return {'success': True, 'name': ws_name, 'history': items}
    except Exception as e:
        return {'success': False, 'error': str(e)}

def extract_spectrum_data(ws_name, spectra, max_points=5000):
    """Extract X/Y/E data for given spectrum indices.
    
    spectra: list of int spectrum indices.
    Returns dict with traces list [{x, y, e, spectrum_index, label}].
    """
    if not MANTID_AVAILABLE or ADS is None:
        return {'success': False, 'error': 'Mantid not available'}
    if not ADS.doesExist(ws_name):
        return {'success': False, 'error': f'Workspace "{ws_name}" not found'}
    try:
        ws = ADS.retrieve(ws_name)
        n_hist = ws.getNumberHistograms()
        n_bins = ws.blocksize()
        
        # Axis labels
        x_unit = ws.getAxis(0).getUnit().caption()
        x_unit_label = ws.getAxis(0).getUnit().label()
        y_unit = ws.YUnitLabel() if hasattr(ws, 'YUnitLabel') else 'Counts'
        
        traces = []
        for si in spectra:
            if si < 0 or si >= n_hist:
                continue
            x = ws.readX(si)
            y = ws.readY(si)
            e = ws.readE(si)
            
            # Bin-centre X for histograms
            if len(x) == len(y) + 1:
                x = [(x[j] + x[j+1]) / 2.0 for j in range(len(y))]
            else:
                x = list(x)
            y = list(y)
            e = list(e)
            
            # Downsample if too large
            step = max(1, len(y) // max_points)
            if step > 1:
                x = x[::step]
                y = y[::step]
                e = e[::step]
            
            # Filter NaN/Inf
            clean_x, clean_y, clean_e = [], [], []
            for xi, yi, ei in zip(x, y, e):
                import math
                if math.isfinite(yi):
                    clean_x.append(xi)
                    clean_y.append(yi)
                    clean_e.append(ei)
            
            traces.append({
                'x': clean_x,
                'y': clean_y,
                'e': clean_e,
                'spectrum_index': si,
                'label': f'Spectrum {si}',
            })
        
        return {
            'success': True,
            'name': ws_name,
            'traces': traces,
            'x_label': f'{x_unit} ({x_unit_label})' if x_unit_label else x_unit,
            'y_label': y_unit,
            'num_spectra': n_hist,
            'num_bins': n_bins,
        }
    except Exception as e:
        return {'success': False, 'error': str(e)}

def extract_colorfill_data(ws_name, max_spectra=500, max_bins=2000):
    """Extract 2D array for colorfill plot.
    
    Returns dict with z (2D array), x_edges, y (spectrum indices),
    and axis labels.
    """
    if not MANTID_AVAILABLE or ADS is None:
        return {'success': False, 'error': 'Mantid not available'}
    if not ADS.doesExist(ws_name):
        return {'success': False, 'error': f'Workspace "{ws_name}" not found'}
    try:
        ws = ADS.retrieve(ws_name)
        n_hist = ws.getNumberHistograms()
        n_bins = ws.blocksize()
        
        # Axis labels
        x_unit = ws.getAxis(0).getUnit().caption()
        x_unit_label = ws.getAxis(0).getUnit().label()
        y_unit = 'Spectrum Index'
        
        # Downsample if needed
        spec_step = max(1, n_hist // max_spectra)
        bin_step = max(1, n_bins // max_bins)
        
        spec_indices = list(range(0, n_hist, spec_step))
        
        # Get x axis (bin centres from first spectrum)
        x0 = ws.readX(0)
        if len(x0) == n_bins + 1:
            x = [(x0[j] + x0[j+1]) / 2.0 for j in range(0, n_bins, bin_step)]
        else:
            x = list(x0[::bin_step])
        
        z = []
        for si in spec_indices:
            row = list(ws.readY(si)[::bin_step])
            z.append(row)
        
        return {
            'success': True,
            'name': ws_name,
            'z': z,
            'x': x,
            'y': spec_indices,
            'x_label': f'{x_unit} ({x_unit_label})' if x_unit_label else x_unit,
            'y_label': y_unit,
            'num_spectra': n_hist,
            'num_bins': n_bins,
        }
    except Exception as e:
        return {'success': False, 'error': str(e)}

def extract_table_data(ws_name, start_spec=0, num_spec=20, max_bins=500):
    """Extract a page of X/Y/E data for table view.
    
    Returns dict with columns and rows for the requested slice.
    """
    if not MANTID_AVAILABLE or ADS is None:
        return {'success': False, 'error': 'Mantid not available'}
    if not ADS.doesExist(ws_name):
        return {'success': False, 'error': f'Workspace "{ws_name}" not found'}
    try:
        ws = ADS.retrieve(ws_name)
        n_hist = ws.getNumberHistograms()
        n_bins = ws.blocksize()
        
        end_spec = min(start_spec + num_spec, n_hist)
        
        rows = []
        for si in range(start_spec, end_spec):
            x = ws.readX(si)
            y = ws.readY(si)
            e = ws.readE(si)
            
            # Bin centres for histograms
            if len(x) == len(y) + 1:
                x = [(x[j] + x[j+1]) / 2.0 for j in range(len(y))]
            else:
                x = list(x)
            
            # Truncate to max_bins
            x = list(x[:max_bins])
            y = list(y[:max_bins])
            e = list(e[:max_bins])
            
            rows.append({
                'spectrum': si,
                'x': x,
                'y': y,
                'e': e,
            })
        
        return {
            'success': True,
            'name': ws_name,
            'rows': rows,
            'start_spec': start_spec,
            'end_spec': end_spec,
            'num_spectra': n_hist,
            'num_bins': n_bins,
            'truncated_bins': n_bins > max_bins,
        }
    except Exception as e:
        return {'success': False, 'error': str(e)}

def extract_sample_logs(ws_name):
    """Extract sample log names, types, and values from a workspace."""
    if not MANTID_AVAILABLE or ADS is None:
        return {'success': False, 'error': 'Mantid not available'}
    if not ADS.doesExist(ws_name):
        return {'success': False, 'error': f'Workspace "{ws_name}" not found'}
    try:
        ws = ADS.retrieve(ws_name)
        run = ws.run()
        logs = []
        for prop in run.getProperties():
            log_info = {
                'name': prop.name,
                'type': type(prop).__name__,
                'units': prop.units if hasattr(prop, 'units') else '',
            }
            # Scalar or short string values
            if hasattr(prop, 'value'):
                val = prop.value
                if isinstance(val, (int, float, bool)):
                    log_info['value'] = val
                    log_info['is_series'] = False
                elif isinstance(val, str) and len(val) < 200:
                    log_info['value'] = val
                    log_info['is_series'] = False
                else:
                    log_info['is_series'] = True
                    log_info['size'] = len(val) if hasattr(val, '__len__') else 0
            else:
                log_info['is_series'] = False
                log_info['value'] = str(prop)[:200]
            logs.append(log_info)
        
        return {'success': True, 'name': ws_name, 'logs': logs}
    except Exception as e:
        return {'success': False, 'error': str(e)}

def extract_log_series(ws_name, log_name):
    """Extract time-series data for a specific sample log."""
    if not MANTID_AVAILABLE or ADS is None:
        return {'success': False, 'error': 'Mantid not available'}
    if not ADS.doesExist(ws_name):
        return {'success': False, 'error': f'Workspace "{ws_name}" not found'}
    try:
        ws = ADS.retrieve(ws_name)
        run = ws.run()
        prop = run.getProperty(log_name)
        
        times = prop.times  # numpy array of datetime64
        values = prop.value  # numpy array of values
        
        # Convert to JSON-serialisable lists
        # times -> ISO strings
        import numpy as np
        t_list = []
        for t in times:
            # DateAndTime objects -> string
            t_list.append(str(t))
        v_list = [float(v) if np.isfinite(v) else None for v in values]
        
        return {
            'success': True,
            'name': ws_name,
            'log_name': log_name,
            'times': t_list,
            'values': v_list,
            'units': prop.units if hasattr(prop, 'units') else '',
        }
    except Exception as e:
        return {'success': False, 'error': str(e)}

def save_workspace_nexus(ws_name, filepath):
    """Save a workspace to a NeXus file."""
    if not MANTID_AVAILABLE or ADS is None:
        return {'success': False, 'error': 'Mantid not available'}
    if not ADS.doesExist(ws_name):
        return {'success': False, 'error': f'Workspace "{ws_name}" not found'}
    try:
        SaveNexus(InputWorkspace=ws_name, Filename=filepath)
        return {'success': True, 'name': ws_name, 'path': filepath}
    except Exception as e:
        return {'success': False, 'error': str(e)}

# Main loop - read JSON commands from stdin, write JSON responses to stdout
while True:
    try:
        line = sys.stdin.readline()
        if not line:
            break
        
        cmd = json.loads(line.strip())
        action = cmd.get('action')
        
        if action == 'execute':
            code = cmd.get('code', '')
            result = execute_code(code)
            print(json.dumps({'type': 'result', **result}), flush=True)
        
        elif action == 'workspaces':
            workspaces = get_workspace_info()
            print(json.dumps({'type': 'workspaces', 'workspaces': workspaces}), flush=True)
        
        elif action == 'variables':
            variables = get_namespace_vars()
            print(json.dumps({'type': 'variables', 'variables': variables}), flush=True)
        
        elif action == 'memory':
            memory_mb = get_mantid_memory_mb()
            print(json.dumps({'type': 'memory', 'mantid_mb': memory_mb}), flush=True)
        
        elif action == 'delete_workspace':
            ws_name = cmd.get('name', '')
            if MANTID_AVAILABLE and ADS is not None and ws_name:
                try:
                    if ADS.doesExist(ws_name):
                        ADS.remove(ws_name)
                        print(json.dumps({'type': 'deleted', 'name': ws_name, 'success': True}), flush=True)
                    else:
                        print(json.dumps({'type': 'deleted', 'name': ws_name, 'success': False, 'error': 'Workspace not found'}), flush=True)
                except Exception as e:
                    print(json.dumps({'type': 'deleted', 'name': ws_name, 'success': False, 'error': str(e)}), flush=True)
            else:
                print(json.dumps({'type': 'deleted', 'name': ws_name, 'success': False, 'error': 'Mantid not available or no name provided'}), flush=True)
        
        elif action == 'rename_workspace':
            result = rename_workspace(cmd.get('old_name', ''), cmd.get('new_name', ''))
            print(json.dumps({'type': 'renamed', **result}), flush=True)
        
        elif action == 'workspace_history':
            result = get_algorithm_history(cmd.get('name', ''))
            print(json.dumps({'type': 'history', **result}), flush=True)
        
        elif action == 'plot_spectrum':
            result = extract_spectrum_data(
                cmd.get('name', ''),
                cmd.get('spectra', [0]),
                cmd.get('max_points', 5000),
            )
            print(json.dumps({'type': 'plot_spectrum', **result}), flush=True)
        
        elif action == 'plot_colorfill':
            result = extract_colorfill_data(
                cmd.get('name', ''),
                cmd.get('max_spectra', 500),
                cmd.get('max_bins', 2000),
            )
            print(json.dumps({'type': 'plot_colorfill', **result}), flush=True)
        
        elif action == 'show_data':
            result = extract_table_data(
                cmd.get('name', ''),
                cmd.get('start_spec', 0),
                cmd.get('num_spec', 20),
                cmd.get('max_bins', 500),
            )
            print(json.dumps({'type': 'show_data', **result}), flush=True)
        
        elif action == 'show_logs':
            result = extract_sample_logs(cmd.get('name', ''))
            print(json.dumps({'type': 'show_logs', **result}), flush=True)
        
        elif action == 'log_series':
            result = extract_log_series(cmd.get('name', ''), cmd.get('log_name', ''))
            print(json.dumps({'type': 'log_series', **result}), flush=True)
        
        elif action == 'save_workspace':
            result = save_workspace_nexus(cmd.get('name', ''), cmd.get('filepath', ''))
            print(json.dumps({'type': 'saved', **result}), flush=True)
        
        elif action == 'ping':
            print(json.dumps({'type': 'pong'}), flush=True)
        
        elif action == 'shutdown':
            print(json.dumps({'type': 'shutdown', 'success': True}), flush=True)
            break
        
        else:
            print(json.dumps({'type': 'error', 'error': f'Unknown action: {action}'}), flush=True)
    
    except json.JSONDecodeError as e:
        print(json.dumps({'type': 'error', 'error': f'Invalid JSON: {e}'}), flush=True)
    except Exception as e:
        print(json.dumps({'type': 'error', 'error': str(e)}), flush=True)
'''

    def stop(self) -> bool:
        """Stop the kernel process."""
        if self._process is None:
            return True

        try:
            # Try graceful shutdown first
            self._send_command({"action": "shutdown"})
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            # Force kill
            self._process.kill()
            self._process.wait()
        except Exception:
            pass

        self._process = None
        self._state = "dead"
        self._start_time = None
        print("[KernelManager] Kernel stopped")
        return True

    def restart(self) -> bool:
        """Restart the kernel process."""
        self.stop()
        return self.start()

    def is_alive(self) -> bool:
        """Check if the kernel process is running."""
        if self._process is None:
            return False
        return self._process.poll() is None

    def _send_command(self, cmd: dict, timeout: float = 60.0) -> Optional[dict]:
        """Send a command to the kernel and get response."""
        if not self.is_alive():
            return None

        try:
            # Send command
            self._process.stdin.write(json.dumps(cmd) + "\n")
            self._process.stdin.flush()

            # Read response with timeout
            # Note: This is simplified - a production version would use
            # select() or async I/O for proper timeout handling
            response_line = self._process.stdout.readline()
            if response_line:
                return json.loads(response_line.strip())
            return None
        except Exception as e:
            print(f"[KernelManager] Command error: {e}")
            return None

    def execute(self, code: str, timeout: float = 60.0) -> ExecutionResult:
        """Execute code in the kernel."""
        with self._exec_lock:
            if not self.is_alive():
                if not self.start():
                    return ExecutionResult(
                        success=False,
                        output="",
                        error="Kernel is not running and failed to start",
                    )

            self._state = "busy"
            start_time = time.time()

            try:
                result = self._send_command({"action": "execute", "code": code}, timeout)
                execution_time = time.time() - start_time

                if result is None:
                    # Kernel may have died
                    self._state = "dead" if not self.is_alive() else "idle"
                    return ExecutionResult(
                        success=False,
                        output="",
                        error="No response from kernel",
                        execution_time=execution_time,
                    )

                self._executions_count += 1
                self._last_execution_time = execution_time
                self._state = "idle"

                output = result.get("output", "")
                error = result.get("error")
                if error:
                    output = output + "\n" + error if output else error

                return ExecutionResult(
                    success=result.get("success", False),
                    output=output,
                    error=error,
                    execution_time=execution_time,
                )
            except Exception as e:
                self._state = "idle" if self.is_alive() else "dead"
                return ExecutionResult(
                    success=False,
                    output="",
                    error=str(e),
                    execution_time=time.time() - start_time,
                )

    def get_status(self) -> KernelStatus:
        """Get current kernel status."""
        uptime = 0.0
        if self._start_time and self.is_alive():
            uptime = time.time() - self._start_time

        return KernelStatus(
            state=self._state if self.is_alive() else "dead",
            pid=self._process.pid if self._process else None,
            uptime_seconds=uptime,
            executions_count=self._executions_count,
            last_execution_time=self._last_execution_time,
        )

    def get_workspaces(self) -> list[WorkspaceInfo]:
        """Get list of workspaces in the kernel."""
        if not self.is_alive():
            return []

        result = self._send_command({"action": "workspaces"})
        if result is None:
            return []

        workspaces = []
        for ws_data in result.get("workspaces", []):
            workspaces.append(
                WorkspaceInfo(
                    name=ws_data.get("name", ""),
                    ws_type=ws_data.get("type", "Unknown"),
                    num_spectra=ws_data.get("num_spectra", 0),
                    num_bins=ws_data.get("num_bins", 0),
                    memory_mb=ws_data.get("memory_mb", 0.0),
                )
            )

        return workspaces

    def get_variables(self) -> list[dict]:
        """Get list of user-defined variables in the kernel namespace."""
        if not self.is_alive():
            return []

        result = self._send_command({"action": "variables"})
        if result is None:
            return []

        return result.get("variables", [])

    def delete_workspace(self, name: str) -> tuple[bool, str]:
        """
        Delete a workspace from the kernel's ADS.

        Returns:
            Tuple of (success: bool, message: str)
        """
        if not self.is_alive():
            return False, "Kernel is not running"

        if not name:
            return False, "Workspace name is required"

        result = self._send_command({"action": "delete_workspace", "name": name})
        if result is None:
            return False, "Failed to communicate with kernel"

        success = result.get("success", False)
        if success:
            return True, f"Workspace '{name}' deleted"
        else:
            error = result.get("error", "Unknown error")
            return False, error

    # ------------------------------------------------------------------
    # Workspace interactivity methods
    # ------------------------------------------------------------------

    def rename_workspace(self, old_name: str, new_name: str) -> Optional[dict]:
        """Rename a workspace in the kernel's ADS."""
        if not self.is_alive():
            return {"success": False, "error": "Kernel is not running"}
        return self._send_command(
            {"action": "rename_workspace", "old_name": old_name, "new_name": new_name}
        )

    def workspace_history(self, name: str) -> Optional[dict]:
        """Get algorithm history for a workspace."""
        if not self.is_alive():
            return {"success": False, "error": "Kernel is not running"}
        return self._send_command({"action": "workspace_history", "name": name})

    def plot_spectrum(
        self, name: str, spectra: list[int], max_points: int = 5000
    ) -> Optional[dict]:
        """Extract spectrum data for plotting."""
        if not self.is_alive():
            return {"success": False, "error": "Kernel is not running"}
        return self._send_command(
            {"action": "plot_spectrum", "name": name, "spectra": spectra, "max_points": max_points}
        )

    def plot_colorfill(self, name: str) -> Optional[dict]:
        """Extract 2D data for colorfill plot."""
        if not self.is_alive():
            return {"success": False, "error": "Kernel is not running"}
        return self._send_command({"action": "plot_colorfill", "name": name})

    def show_data(
        self, name: str, start_spec: int = 0, num_spec: int = 20
    ) -> Optional[dict]:
        """Extract paginated table data."""
        if not self.is_alive():
            return {"success": False, "error": "Kernel is not running"}
        return self._send_command(
            {"action": "show_data", "name": name, "start_spec": start_spec, "num_spec": num_spec}
        )

    def show_logs(self, name: str) -> Optional[dict]:
        """Get sample logs from a workspace."""
        if not self.is_alive():
            return {"success": False, "error": "Kernel is not running"}
        return self._send_command({"action": "show_logs", "name": name})

    def log_series(self, name: str, log_name: str) -> Optional[dict]:
        """Get time-series data for a specific sample log."""
        if not self.is_alive():
            return {"success": False, "error": "Kernel is not running"}
        return self._send_command({"action": "log_series", "name": name, "log_name": log_name})

    def save_workspace(self, name: str, filepath: str) -> Optional[dict]:
        """Save a workspace to NeXus file."""
        if not self.is_alive():
            return {"success": False, "error": "Kernel is not running"}
        return self._send_command({"action": "save_workspace", "name": name, "filepath": filepath})

    def get_memory_info(self) -> MemoryInfo:
        """Get memory usage information."""
        # System memory from psutil
        mem = psutil.virtual_memory()
        system_total_gb = mem.total / (1024**3)
        system_used_gb = mem.used / (1024**3)
        system_percent = mem.percent

        # Mantid memory from kernel
        mantid_mb = 0.0
        if self.is_alive():
            result = self._send_command({"action": "memory"})
            if result:
                mantid_mb = result.get("mantid_mb", 0.0)

        mantid_gb = mantid_mb / 1024
        mantid_percent = (mantid_gb / system_total_gb * 100) if system_total_gb > 0 else 0

        return MemoryInfo(
            system_total_gb=system_total_gb,
            system_used_gb=system_used_gb,
            system_percent=system_percent,
            mantid_used_gb=mantid_gb,
            mantid_percent=mantid_percent,
            warning=system_percent > 85,
            critical=system_percent > 95,
        )


# Global kernel manager instance
_kernel_manager: Optional[KernelManager] = None


def get_kernel_manager() -> KernelManager:
    """Get the global kernel manager instance."""
    global _kernel_manager
    if _kernel_manager is None:
        _kernel_manager = KernelManager()
    return _kernel_manager
