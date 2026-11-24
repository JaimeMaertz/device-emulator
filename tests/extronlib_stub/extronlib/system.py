"""Enough of extronlib.system for catalog device modules to import and run."""
import threading


def ProgramLog(message, level='info'):
    print('ProgramLog [{}]: {}'.format(level, message))


class Wait:
    def __init__(self, Time, Function=None):
        self.Time, self.Function = Time, Function
        self._timer = None
        if Function:
            self.Restart()

    def __call__(self, function):
        self.Function = function
        self.Restart()
        return function

    def Restart(self):
        self.Cancel()
        self._timer = threading.Timer(self.Time, self.Function)
        self._timer.daemon = True
        self._timer.start()

    def Cancel(self):
        if self._timer:
            self._timer.cancel()


class Timer:
    """Calls Function(timer, count) every Interval seconds until stopped, like extronlib's Timer."""

    def __init__(self, Interval, Function=None):
        self.Interval, self.Function = Interval, Function
        self.Count, self.State = 0, 'Stopped'
        self._generation = 0
        if Function:
            self.Restart()

    def __call__(self, function):
        self.Function = function
        self.Restart()
        return function

    def _tick(self, generation):
        if generation != self._generation or self.State != 'Running':
            return
        self.Count += 1
        try:
            self.Function(self, self.Count)
        finally:
            self._schedule(generation)

    def _schedule(self, generation):
        if generation == self._generation and self.State == 'Running':
            timer = threading.Timer(self.Interval, self._tick, (generation,))
            timer.daemon = True
            timer.start()

    def Restart(self):
        self._generation += 1
        self.Count, self.State = 0, 'Running'
        self._schedule(self._generation)

    def Resume(self):
        if self.State != 'Running':
            self.State = 'Running'
            self._generation += 1
            self._schedule(self._generation)

    def Pause(self):
        self.State = 'Paused'

    def Stop(self):
        self.State = 'Stopped'
        self.Count = 0

    def Change(self, Interval):
        self.Interval = Interval
