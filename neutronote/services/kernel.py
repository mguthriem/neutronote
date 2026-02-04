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
            'state': self.state,
            'pid': self.pid,
            'uptime_seconds': self.uptime_seconds,
            'executions_count': self.executions_count,
            'last_execution_time': self.last_execution_time,
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
            'system_total_gb': round(self.system_total_gb, 2),
            'system_used_gb': round(self.system_used_gb, 2),
            'system_percent': round(self.system_percent, 1),
            'mantid_used_gb': round(self.mantid_used_gb, 2),
            'mantid_percent': round(self.mantid_percent, 1),
            'warning': self.warning,
            'critical': self.critical,
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
            'name': self.name,
            'type': self.ws_type,
            'num_spectra': self.num_spectra,
            'num_bins': self.num_bins,
            'memory_mb': round(self.memory_mb, 2),
        }


class KernelManager:
    """
    Manages a persistent Python kernel process.
    
    The kernel runs as a subprocess and maintains state across executions.
    Communication happens via stdin/stdout with JSON messages.
    """
    
    # Singleton instance
    _instance: Optional['KernelManager'] = None
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
        self._state = 'dead'
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
        
        self._state = 'starting'
        
        # The kernel runner script
        kernel_script = self._get_kernel_script()
        
        try:
            self._process = subprocess.Popen(
                [sys.executable, '-c', kernel_script],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,  # Line buffered
            )
            self._start_time = time.time()
            self._state = 'idle'
            self._executions_count = 0
            print(f"[KernelManager] Kernel started with PID {self._process.pid}")
            return True
        except Exception as e:
            print(f"[KernelManager] Failed to start kernel: {e}")
            self._state = 'dead'
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
    """Get list of user-defined variables in the namespace."""
    # Filter out private vars and built-in types
    vars_list = []
    for name, val in _user_namespace.items():
        if not name.startswith('_'):
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
            self._send_command({'action': 'shutdown'})
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            # Force kill
            self._process.kill()
            self._process.wait()
        except Exception:
            pass
        
        self._process = None
        self._state = 'dead'
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
            self._process.stdin.write(json.dumps(cmd) + '\n')
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
                        output='',
                        error='Kernel is not running and failed to start',
                    )
            
            self._state = 'busy'
            start_time = time.time()
            
            try:
                result = self._send_command({'action': 'execute', 'code': code}, timeout)
                execution_time = time.time() - start_time
                
                if result is None:
                    # Kernel may have died
                    self._state = 'dead' if not self.is_alive() else 'idle'
                    return ExecutionResult(
                        success=False,
                        output='',
                        error='No response from kernel',
                        execution_time=execution_time,
                    )
                
                self._executions_count += 1
                self._last_execution_time = execution_time
                self._state = 'idle'
                
                output = result.get('output', '')
                error = result.get('error')
                if error:
                    output = output + '\n' + error if output else error
                
                return ExecutionResult(
                    success=result.get('success', False),
                    output=output,
                    error=error,
                    execution_time=execution_time,
                )
            except Exception as e:
                self._state = 'idle' if self.is_alive() else 'dead'
                return ExecutionResult(
                    success=False,
                    output='',
                    error=str(e),
                    execution_time=time.time() - start_time,
                )
    
    def get_status(self) -> KernelStatus:
        """Get current kernel status."""
        uptime = 0.0
        if self._start_time and self.is_alive():
            uptime = time.time() - self._start_time
        
        return KernelStatus(
            state=self._state if self.is_alive() else 'dead',
            pid=self._process.pid if self._process else None,
            uptime_seconds=uptime,
            executions_count=self._executions_count,
            last_execution_time=self._last_execution_time,
        )
    
    def get_workspaces(self) -> list[WorkspaceInfo]:
        """Get list of workspaces in the kernel."""
        if not self.is_alive():
            return []
        
        result = self._send_command({'action': 'workspaces'})
        if result is None:
            return []
        
        workspaces = []
        for ws_data in result.get('workspaces', []):
            workspaces.append(WorkspaceInfo(
                name=ws_data.get('name', ''),
                ws_type=ws_data.get('type', 'Unknown'),
                num_spectra=ws_data.get('num_spectra', 0),
                num_bins=ws_data.get('num_bins', 0),
                memory_mb=ws_data.get('memory_mb', 0.0),
            ))
        
        return workspaces
    
    def get_variables(self) -> list[dict]:
        """Get list of user-defined variables in the kernel namespace."""
        if not self.is_alive():
            return []
        
        result = self._send_command({'action': 'variables'})
        if result is None:
            return []
        
        return result.get('variables', [])
    
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
        
        result = self._send_command({'action': 'delete_workspace', 'name': name})
        if result is None:
            return False, "Failed to communicate with kernel"
        
        success = result.get('success', False)
        if success:
            return True, f"Workspace '{name}' deleted"
        else:
            error = result.get('error', 'Unknown error')
            return False, error
    
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
            result = self._send_command({'action': 'memory'})
            if result:
                mantid_mb = result.get('mantid_mb', 0.0)
        
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
