from com.arm.debug.dtsl.configurations import DTSLv1
from com.arm.debug.dtsl.components import FormatterMode
from com.arm.debug.dtsl.components import AxBMemAPAccessor
from com.arm.debug.dtsl.components import Device
from com.arm.debug.dtsl.components import AHBAP
from com.arm.debug.dtsl.components import APBAP
from com.arm.debug.dtsl.configurations.options import IIntegerOption
from com.arm.debug.dtsl.components import DSTREAMTraceCapture
from com.arm.debug.dtsl.components import CSCTI
from com.arm.debug.dtsl.components import CTISyncSMPDevice
from com.arm.debug.dtsl.components import PTMTraceSource
from com.arm.debug.dtsl.components import CSTPIU
from com.arm.debug.dtsl.components import CSFunnel

NUM_CORES_CORTEX_A9 = 2
TRACE_RANGE_DESCRIPTION = '''Limit trace capture to the specified range. This is useful for restricting trace capture to an OS (e.g. Linux kernel)'''
ATB_ID_BASE = 2
DSTREAM_PORTWIDTH = 16
CORTEX_A9_TRACE_OPTIONS = 0
CTM_CHANNEL_SYNC_STOP = 0  # use channel 0 for sync stop
CTM_CHANNEL_SYNC_START = 1  # use channel 1 for sync start
CTM_CHANNEL_TRACE_TRIGGER = 2  # use channel 2 for trace triggers

class TraceRangeOptions:
    def __init__(self, coreTraceName = None, dtsl = None):
        if coreTraceName == None:
            self.defaultSetup()
        else:
            self.traceRangeEnable = dtsl.getOptionValue("%s.traceRange" % coreTraceName)
            self.traceRangeStart = dtsl.getOptionValue("%s.traceRange.start" % coreTraceName)
            self.traceRangeEnd = dtsl.getOptionValue("%s.traceRange.end" % coreTraceName)
            self.traceRangeIDs = None

    def defaultSetup(self):
        self.traceRangeEnable = False
        self.traceRangeStart = None
        self.traceRangeEnd = None
        self.traceRangeIDs = None


class DtslScript(DTSLv1):
    @staticmethod
    def getOptionList():
        return [
            DTSLv1.enumOption('traceCapture', 'Trace capture method', defaultValue="none",
                values = [("none", "None"), ("DSTREAM", "DSTREAM 4GB Trace Buffer")],
                setter=DtslScript.setTraceCaptureMethod),
            DTSLv1.booleanOption('cortexA9coreTrace', 'Enable Cortex-A9 core trace', defaultValue=False,
                childOptions =
                    # Allow each source to be enabled/disabled individually
                    [ DTSLv1.booleanOption('Cortex_A9_%d' % c, "Enable Cortex-A9 %d trace" % c, defaultValue=True)
                    for c in range(0, NUM_CORES_CORTEX_A9) ] +
                    # Pull in common options for PTMs (cycle accurate etc)
                    PTMTraceSource.defaultOptions(DtslScript.getPTMs) +
                    [ # Trace range selection (e.g. for linux kernel)
                    DTSLv1.booleanOption('traceRange', 'Trace capture range',
                        description=TRACE_RANGE_DESCRIPTION,
                        defaultValue = False,
                        childOptions = [
                            DTSLv1.integerOption('start', 'Start address',
                                description='Start address for trace capture',
                                defaultValue=0,
                                display=IIntegerOption.DisplayFormat.HEX),
                            DTSLv1.integerOption('end', 'End address',
                                description='End address for trace capture',
                                defaultValue=0xFFFFFFFF,
                                display=IIntegerOption.DisplayFormat.HEX)
                        ])
                    ]
                ),
        ]

    def __init__(self, root):
        DTSLv1.__init__(self, root)

        # locate devices on the platform and create corresponding objects
        self.discoverDevices()

        self.mgdPlatformDevs = set()

        # tracks which devices are managed when a trace mode is enabled
        self.mgdTraceDevs = {}

        # only AHB/APB are managed by default - others will be added when enabling trace, SMP etc
        self.mgdPlatformDevs.add(self.AHB)
        self.mgdPlatformDevs.add(self.APB)

        self.exposeCores()

        self.setupDSTREAMTrace(DSTREAM_PORTWIDTH)

        self.setupCTISyncSMP()

        # use internal trace range to limit trace to e.g. kernel
        self.traceRangeOptions = [
            TraceRangeOptions(), # Cortex-A9 trace options
            ]

        self.setManagedDevices(self.mgdPlatformDevs)

    def discoverDevices(self):
        '''find and create devices'''

        ahbDev = self.findDevice("CSMEMAP")
        self.AHB = AHBAP(self, ahbDev, "CSMEMAP")

        apbDev = self.findDevice("CSMEMAP", ahbDev+1)
        self.APB = APBAP(self, apbDev, "CSMEMAP")

        cortexA9coreDev = 0
        self.cortexA9cores = []

        streamID = ATB_ID_BASE

        # Trace start/stop CTI
        outCTIDev = self.findDevice("CSCTI")
        self.outCTI = CSCTI(self, outCTIDev, "CTI_out")
        coreCTIDev = outCTIDev
        self.CTIs  = []
        self.cortexA9ctiMap = {} # map cores to associated CTIs

        ptmDev = 1
        self.PTMs  = []

        for i in range(0, NUM_CORES_CORTEX_A9):
            # create core
            cortexA9coreDev = self.findDevice("Cortex-A9", cortexA9coreDev+1)
            dev = Device(self, cortexA9coreDev, "Cortex-A9_%d" % i)
            self.cortexA9cores.append(dev)

            # create CTI for this core
            coreCTIDev = self.findDevice("CSCTI", coreCTIDev+1)
            coreCTI = CSCTI(self, coreCTIDev, "CTI_%d" % i)
            self.CTIs.append(coreCTI)
            self.cortexA9ctiMap[dev] = coreCTI

            # create the PTM for this core
            ptmDev = self.findDevice("CSPTM", ptmDev+1)
            ptm = PTMTraceSource(self, ptmDev, streamID, "PTM_%d" % i)
            streamID += 1
            # disabled by default - will enable with option
            ptm.setEnabled(False)
            self.PTMs.append(ptm)

        # DSTREAM
        self.DSTREAM = DSTREAMTraceCapture(self, "DSTREAM")

        # TPIU
        tpiuDev = self.findDevice("CSTPIU")
        self.tpiu = self.createTPIU(tpiuDev, "TPIU")

        # Funnel 0
        funnelDev0 = self.findDevice("CSTFunnel")
        self.funnel0 = self.createFunnel(funnelDev0, "Funnel_0")

    def addManagedPlatformDevices(self, devs):
        '''Add devices to the set of devices managed by the configuration'''
        for d in devs:
            self.mgdPlatformDevs.add(d)

    def getManagedDevices(self, traceKey):
        '''Get the required set of managed devices for this configuration'''
        return self.mgdPlatformDevs | self.mgdTraceDevs.get(traceKey, set())

    def registerTraceSource(self, traceCapture, source):
        '''Register trace source with trace capture device'''
        traceCapture.addTraceSource(source)
        self.addManagedTraceDevices(traceCapture.getName(), [ source ])

    def addManagedTraceDevices(self, traceKey, devs):
        '''Add devices to the set of devices managed by the configuration for this trace mode'''
        traceDevs = self.mgdTraceDevs.get(traceKey)
        if not traceDevs:
            traceDevs = set()
            self.mgdTraceDevs[traceKey] = traceDevs
        for d in devs:
            traceDevs.add(d)

    def registerFilters(self, core):
        '''Register MemAP filters to allow access to the AHB/APB for the device'''
        core.registerAddressFilters(
            [AxBMemAPAccessor("AHB", self.AHB, "AHB bus accessed via AP_0"),
             AxBMemAPAccessor("APB", self.APB, "APB bus accessed via AP_1")])

    def setTraceCaptureMethod(self, method):
        if method == "none":
            self.setDSTREAMTraceEnabled(False)
        elif method == "DSTREAM":
            self.setDSTREAMTraceEnabled(True)

    def exposeCores(self):
        for core in self.cortexA9cores:
            self.registerFilters(core)
            self.addDeviceInterface(core)

    def setupDSTREAMTrace(self, portwidth):
        '''Setup DSTREAM trace capture'''

        # configure the TPIU for continuous mode
        self.tpiu.setFormatterMode(FormatterMode.CONTINUOUS)
        self.tpiu.setPortSize(portwidth)

        # configure the DSTREAM for continuous trace
        self.DSTREAM.setTraceMode(DSTREAMTraceCapture.TraceMode.Continuous)
        self.DSTREAM.setPortWidth(portwidth)

        # register other trace components
        self.DSTREAM.setTraceComponentOrder([ self.funnel0, self.tpiu ])

        # register the DSTREAM with the configuration
        self.addTraceCaptureInterface(self.DSTREAM)

        # automatically handle connection/disconnection to trace components
        self.addManagedTraceDevices("DSTREAM", [ self.funnel0, self.outCTI, self.tpiu, self.DSTREAM ])

        # register trace sources
        self.registerTraceSources(self.DSTREAM)

    def enableCTIInput(self, cti, input, channel, enabled):
        '''Enable/disable cross triggering between an input and a channel'''
        if enabled:
            cti.enableInputEvent(input, channel)
        else:
            cti.disableInputEvent(input, channel)

    def enableCTIsForSink(self, sink, enabled):
        '''Enable/disable triggers using CTI associated with source'''
        cti, output, channel = self.getCTIForSink(sink)
        if cti:
            self.enableCTIOutput(cti, output, channel, enabled)

    def enableCTIsForSource(self, source, enabled):
        '''Enable/disable triggers using CTI associated with source'''
        cti, input, channel = self.getCTIForSource(source)
        if cti:
            self.enableCTIInput(cti, input, channel, enabled)

    def enableCTIOutput(self, cti, output, channel, enabled):
        '''Enable/disable cross triggering between a channel and an output'''
        if enabled:
            cti.enableOutputEvent(output, channel)
        else:
            cti.disableOutputEvent(output, channel)

    def setupCTISyncSMP(self):
        '''Create SMP device using CTI synchronization'''

        # Setup CTIs for synch start/stop
        # Cortex-A9 CTI SMP setup
        ctiInfo = {}
        for c in self.cortexA9cores:
            # use standard Cortex event mapping : in/out on trigger 0 for stop, out on trigger 7 for start
            ctiInfo[c] = CTISyncSMPDevice.DeviceCTIInfo(self.cortexA9ctiMap[c], CTISyncSMPDevice.DeviceCTIInfo.NONE, 7, 0, 0)
        smp = CTISyncSMPDevice(self, "Cortex-A9 SMP", self.cortexA9cores, ctiInfo, CTM_CHANNEL_SYNC_START, CTM_CHANNEL_SYNC_STOP)
        self.registerFilters(smp)
        self.addDeviceInterface(smp)

        # automatically handle connection to CTIs
        self.addManagedPlatformDevices(self.CTIs)

    def getCTIForSink(self, sink):
        '''Get the CTI and input/channel associated with a trace sink
        return (None, None, None) if no associated CTI
        '''
        if sink == self.DSTREAM:
            # TPIU trigger input is CTI out 3
            return (self.outCTI, 3, CTM_CHANNEL_TRACE_TRIGGER)
        # no associated CTI
        return (None, None, None)

    def getCTIForSource(self, source):
        '''Get the CTI and input/channel associated with a source
        return (None, None, None) if no associated CTI
        '''
        if source in self.PTMs:
            coreNum = self.PTMs.index(source)
            # PTM trigger is on input 6
            return (self.CTIs[coreNum], 6, CTM_CHANNEL_TRACE_TRIGGER)

        # no associated CTI
        return (None, None, None)

    def setTraceSourceEnabled(self, source, enabled):
        '''Enable/disable a trace source'''
        source.setEnabled(enabled)
        self.enableFunnelPortForSource(source, enabled)
        self.enableCTIsForSource(source, enabled)

    def getPTMs(self):
        '''Get the PTMs'''
        return self.PTMs

    def createTPIU(self, tpiuDev, name):
        tpiu = CSTPIU(self, tpiuDev, name)
        # disable the TPIU by default to allow ETB to work at full rate
        tpiu.setEnabled(False)
        return tpiu

    def createFunnel(self, funnelDev, name):
        funnel = CSFunnel(self, funnelDev, name)
        funnel.setAllPortsDisabled() # Will enable for each source later
        return funnel

    def enableFunnelPortForSource(self, source, enabled):
        '''Enable/disable the funnel port for a trace source'''
        port = self.getFunnelPortForSource(source)
        if enabled:
            self.funnel0.setPortEnabled(port)
        else:
            self.funnel0.setPortDisabled(port)

    def getFunnelPortForCore(self, core):
        ''' Funnel port-to-core mapping can be customized here'''
        port = core
        return port

    def setDSTREAMTraceEnabled(self, enabled):
        '''Enable/disable DSTREAM trace capture'''
        self.tpiu.setEnabled(enabled)
        self.enableCTIsForSink(self.DSTREAM, enabled)

    def registerTraceSources(self, traceCapture):
        '''Register all trace sources with trace capture device'''
        for c in range(0, NUM_CORES_CORTEX_A9):
            self.registerCoreTraceSource(traceCapture, self.cortexA9cores[c], self.PTMs[c])


    def registerCoreTraceSource(self, traceCapture, core, source):
        '''Register a trace source with trace capture device and enable triggers'''
        # Register with trace capture, associating with core
        traceCapture.addTraceSource(source, core.getID())

        # source is managed by the configuration
        self.addManagedTraceDevices(traceCapture.getName(), [ source ])

        # CTI (if present) is also managed by the configuration
        cti, input, channel = self.getCTIForSource(source)
        if cti:
            self.addManagedTraceDevices(traceCapture.getName(), [ cti ])

    def setCoreTraceEnabled(self, enabled):
        '''Enable/disable the core trace sources'''
        for t in self.PTMs:
            self.setTraceSourceEnabled(t, enabled)


    def getFunnelPortForSource(self, source):
        '''Get the funnel port number for a trace source'''

        # Build map of sources to funnel ports
        portMap = {}
        for i in range(0, NUM_CORES_CORTEX_A9):
            portMap[self.PTMs[i]] = self.getFunnelPortForCore(i)


        return portMap.get(source, None)

    def setInternalTraceRange(self, currentTraceOptions, newTraceOptions, traceMacrocells):
        # values are different to current config
        if (newTraceOptions.traceRangeEnable != currentTraceOptions.traceRangeEnable) or \
            (newTraceOptions.traceRangeStart != currentTraceOptions.traceRangeStart) or \
            (newTraceOptions.traceRangeEnd != currentTraceOptions.traceRangeEnd):

            # clear existing ranges
            if currentTraceOptions.traceRangeIDs:
                for i in range(0, len(traceMacrocells)):
                    traceMacrocells[i].clearTraceRange(currentTraceOptions.traceRangeIDs[i])
                currentTraceOptions.traceRangeIDs = None

            # set new ranges
            if newTraceOptions.traceRangeEnable:
                currentTraceOptions.traceRangeIDs = [
                    traceMacrocells[i].addTraceRange(newTraceOptions.traceRangeStart, newTraceOptions.traceRangeEnd)
                    for i in range(0, len(traceMacrocells))
                    ]

            currentTraceOptions.traceRangeEnable = newTraceOptions.traceRangeEnable
            currentTraceOptions.traceRangeStart = newTraceOptions.traceRangeStart
            currentTraceOptions.traceRangeEnd = newTraceOptions.traceRangeEnd

    def optionValuesChanged(self):
        '''Callback to update the configuration state after options are changed'''
        optionValues = self.getOptionValues()
        traceMode = optionValues.get("traceCapture")
        self.setManagedDevices(self.getManagedDevices(traceMode))

        coreTraceEnabled = self.getOptionValue("cortexA9coreTrace")
        for i in range(0, NUM_CORES_CORTEX_A9):
            thisCoreTraceEnabled = self.getOptionValue("cortexA9coreTrace.Cortex_A9_%d" % i)
            enableSource = coreTraceEnabled and thisCoreTraceEnabled
            self.setTraceSourceEnabled(self.PTMs[i], enableSource)

        ptmStartIndex = 0
        ptmEndIndex = 0

        ptmEndIndex += NUM_CORES_CORTEX_A9
        self.setInternalTraceRange(self.traceRangeOptions[CORTEX_A9_TRACE_OPTIONS], TraceRangeOptions("cortexA9coreTrace", self), self.PTMs[ptmStartIndex:ptmEndIndex])
        ptmStartIndex += NUM_CORES_CORTEX_A9


class DtslScript_NoDSTREAM(DtslScript):
    @staticmethod
    def getOptionList():
        return []

class DtslScript_RVI(DtslScript_NoDSTREAM):
    pass

class DtslScript_ULINKPro(DtslScript_NoDSTREAM):
    pass

class DtslScript_ULINKPro_D(DtslScript_NoDSTREAM):
    pass
