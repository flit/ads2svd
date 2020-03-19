from com.arm.debug.dtsl.configurations import DTSLv1
from com.arm.debug.dtsl.components import FormatterMode
from com.arm.debug.dtsl.components import AxBMemAPAccessor
from com.arm.debug.dtsl.components import Device
from com.arm.debug.dtsl.components import AHBAP
from com.arm.debug.dtsl.components import APBAP
from com.arm.debug.dtsl.configurations.options import IIntegerOption
from com.arm.debug.dtsl.components import ETBTraceCapture
from com.arm.debug.dtsl.components import DSTREAMTraceCapture
from com.arm.debug.dtsl.components import CSCTI
from com.arm.debug.dtsl.components import PTMTraceSource
from com.arm.debug.dtsl.components import CSTPIU
from com.arm.debug.dtsl.components import CSFunnel
from com.arm.debug.dtsl.components import ITMTraceSource
from com.arm.debug.dtsl.components import RDDISyncSMPDevice
from com.arm.debug.dtsl.interfaces import IARMCoreTraceSource

NUM_CORES_CORTEX_A9 = 2
ATB_ID_BASE = 2
TRACE_RANGE_DESCRIPTION = '''Limit trace capture to the specified range. This is useful for restricting trace capture to an OS (e.g. Linux kernel)'''
DSTREAM_PORTWIDTH = 16
CTM_CHANNEL_SYNC_STOP = 0  # use channel 0 for sync stop
CTM_CHANNEL_SYNC_START = 1  # use channel 1 for sync start
CTM_CHANNEL_TRACE_TRIGGER = 2  # use channel 2 for trace triggers
ITM_ATB_ID = 1
ITM_FUNNEL_PORT = 3
CORTEX_A9_TRACE_OPTIONS = 0

class TraceRangeOptions:
    def __init__(self, coreTraceName = None, dtsl = None):
        if coreTraceName == None:
            self.defaultSetup()
        else:
            self.traceRangeEnable = dtsl.getOptionValue("options.trace.%s.traceRange" % coreTraceName)
            self.traceRangeStart = dtsl.getOptionValue("options.trace.%s.traceRange.start" % coreTraceName)
            self.traceRangeEnd = dtsl.getOptionValue("options.trace.%s.traceRange.end" % coreTraceName)
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
            DTSLv1.tabSet("options", "Options", childOptions=[
                DTSLv1.tabPage("trace", "Trace", childOptions=[
                    DTSLv1.enumOption('traceCapture', 'Trace capture method', defaultValue="none",
                        values = [("none", "None"), ("ETB", "On Chip Trace Buffer (ETB)"), ("DSTREAM", "DSTREAM 4GB Trace Buffer")],
                        setter=DtslScript.setTraceCaptureMethod),
                    DTSLv1.booleanOption('cortexA9coreTrace', 'Enable Cortex-A9 core trace', defaultValue=False,
                        childOptions =
                            # Allow each source to be enabled/disabled individually
                            [ DTSLv1.booleanOption('Cortex_A9_%d' % c, "Enable Cortex-A9 %d trace" % c, defaultValue=True)
                            for c in range(0, NUM_CORES_CORTEX_A9) ] +
                            [ DTSLv1.booleanOption('triggerhalt', "PTM Triggers halt execution", description="Enable the PTM triggers to halt execution", defaultValue=False) ] +
                            [ DTSLv1.booleanOption('contextIDs', "Enable PTM Context IDs", description="Controls the output of context ID values into the PTM output streams", defaultValue=True,
                                childOptions = [
                                    DTSLv1.enumOption('contextIDsSize', 'Context ID Size', defaultValue="32",
                                        values = [("8", "8 bit"), ("16", "16 bit"), ("32", "32 bit")])
                                    ])
                            ] +
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
                    DTSLv1.booleanOption('itm', 'Enable ITM trace', defaultValue=False,
                        setter=DtslScript.setITMEnabled),
                ])
            ])
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

        self.setupETBTrace()

        self.setupDSTREAMTrace(DSTREAM_PORTWIDTH)

        self.setupSimpleSyncSMP()
        # use internal trace range to limit trace to e.g. kernel
        self.traceRangeOptions = [
            TraceRangeOptions(), # Cortex-A9 trace options
            ]

        self.setManagedDevices(self.mgdPlatformDevs)

    # +----------------------------+
    # | Target dependent functions |
    # +----------------------------+

    def discoverDevices(self):
        '''find and create devices'''

        ahbDev = self.findDevice("CSMEMAP")
        self.AHB = AHBAP(self, ahbDev, "CSMEMAP")

        apbDev = self.findDevice("CSMEMAP", ahbDev+1)
        self.APB = APBAP(self, apbDev, "CSMEMAP")

        cortexA9coreDev = 0
        self.cortexA9cores = []

        streamID = ATB_ID_BASE

        ptmDev = 1
        self.PTMs  = []

        for i in range(0, NUM_CORES_CORTEX_A9):
            # create core
            cortexA9coreDev = self.findDevice("Cortex-A9", cortexA9coreDev+1)
            dev = Device(self, cortexA9coreDev, "Cortex-A9_%d" % i)
            self.cortexA9cores.append(dev)

            # create the PTM for this core
            ptmDev = self.findDevice("CSPTM", ptmDev+1)
            ptm = PTMTraceSource(self, ptmDev, streamID, "PTM_%d_%d" % (i, streamID))
            streamID += 1
            # disabled by default - will enable with option
            ptm.setEnabled(False)
            self.PTMs.append(ptm)

        # Output CTI
        ctiDev = self.findDevice("CSCTI")
        self.outCTI = CSCTI(self, ctiDev, "CTI_out")

        # ETB
        etbDev = self.findDevice("CSETB")
        self.ETB = ETBTraceCapture(self, etbDev, "ETB")

        # DSTREAM
        self.DSTREAM = DSTREAMTraceCapture(self, "DSTREAM")

        # TPIU
        tpiuDev = self.findDevice("CSTPIU")
        self.tpiu = self.createTPIU(tpiuDev, "TPIU")

        # Funnel 0
        funnelDev0 = self.findDevice("CSTFunnel")
        self.funnel0 = self.createFunnel(funnelDev0, "Funnel_0")

        # ITM
        itmDev = self.findDevice("CSITM")
        self.ITM = self.createITM(itmDev, ITM_ATB_ID, "ITM")

    def exposeCores(self):
        for core in self.cortexA9cores:
            self.registerFilters(core)
            self.addDeviceInterface(core)

    def setupETBTrace(self):
        '''Setup ETB trace capture'''

        # use continuous mode
        self.ETB.setFormatterMode(FormatterMode.CONTINUOUS)

        # register other trace components with ETB and register ETB with configuration
        self.ETB.setTraceComponentOrder([ self.funnel0 ])
        self.addTraceCaptureInterface(self.ETB)

        # automatically handle connection/disconnection to trace components
        self.addManagedTraceDevices("ETB", [ self.funnel0, self.outCTI, self.tpiu, self.ETB ])

        # register trace sources
        self.registerTraceSources(self.ETB)

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

    def getCTIForSink(self, sink):
        '''Get the CTI and input/channel associated with a trace sink
        return (None, None, None) if no associated CTI
        '''
        if sink == self.ETB:
            # ETB trigger input is CTI out 1
            return (self.outCTI, 1, CTM_CHANNEL_TRACE_TRIGGER)
        if sink == self.DSTREAM:
            # TPIU trigger input is CTI out 3
            return (self.outCTI, 3, CTM_CHANNEL_TRACE_TRIGGER)
        # no associated CTI
        return (None, None, None)

    def getCTIForSource(self, source):
        '''Get the CTI and input/channel associated with a source'''
        # no associated CTI
        return (None, None, None)

    def enableCTIOutput(self, cti, output, channel, enabled):
        '''Enable/disable cross triggering between a channel and an output'''
        if enabled:
            cti.enableOutputEvent(output, channel)
        else:
            cti.disableOutputEvent(output, channel)

    def setTraceSourceEnabled(self, source, enabled):
        '''Enable/disable a trace source'''
        source.setEnabled(enabled)
        self.enableFunnelPortForSource(source, enabled)
        self.enableCTIsForSource(source, enabled)

    def createTPIU(self, tpiuDev, name):
        tpiu = CSTPIU(self, tpiuDev, name)
        # disable the TPIU by default to allow ETB to work at full rate
        tpiu.setEnabled(False)
        return tpiu

    def setupSimpleSyncSMP(self):
        '''Create SMP device using RDDI synchronization'''

        # create SMP device and expose from configuration
        # Cortex-A9x2 SMP
        smp = RDDISyncSMPDevice(self, "Cortex-A9 SMP", self.cortexA9cores)
        self.registerFilters(smp)
        self.addDeviceInterface(smp)

    def setETBTraceEnabled(self, enabled):
        '''Enable/disable ETB trace capture'''
        self.enableCTIsForSink(self.ETB, enabled)

    def setDSTREAMTraceEnabled(self, enabled):
        '''Enable/disable DSTREAM trace capture'''
        self.tpiu.setEnabled(enabled)
        self.enableCTIsForSink(self.DSTREAM, enabled)

    def registerTraceSources(self, traceCapture):
        '''Register all trace sources with trace capture device'''
        for c in range(0, NUM_CORES_CORTEX_A9):
            self.registerCoreTraceSource(traceCapture, self.cortexA9cores[c], self.PTMs[c])

        self.registerTraceSource(traceCapture, self.ITM)

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

    def getFunnelPortForSource(self, source):
        '''Get the funnel port number for a trace source'''

        # Build map of sources to funnel ports
        portMap = {self.ITM: ITM_FUNNEL_PORT}
        for i in range(0, NUM_CORES_CORTEX_A9):
            portMap[self.PTMs[i]] = self.getFunnelPortForCore(i)

        return portMap.get(source, None)

    # +--------------------------------+
    # | Callback functions for options |
    # +--------------------------------+

    def setTriggerGeneratesDBGRQ(self, xtm, state):
        xtm.setTriggerGeneratesDBGRQ(state)

    def setContextIDEnabled(self, xtm, state, size):
        if state == False:
            xtm.setContextIDs(False, IARMCoreTraceSource.ContextIDSize.NONE)
        else:
            contextIDSizeMap = {
                 "8":IARMCoreTraceSource.ContextIDSize.BITS_7_0,
                "16":IARMCoreTraceSource.ContextIDSize.BITS_15_0,
                "32":IARMCoreTraceSource.ContextIDSize.BITS_31_0 }
            xtm.setContextIDs(True, contextIDSizeMap[size])

    def optionValuesChanged(self):
        '''Callback to update the configuration state after options are changed'''
        optionValues = self.getOptionValues()
        traceMode = optionValues.get("options.trace.traceCapture")
        self.setManagedDevices(self.getManagedDevices(traceMode))

        coreTraceEnabled = self.getOptionValue("options.trace.cortexA9coreTrace")
        for i in range(0, NUM_CORES_CORTEX_A9):
            thisCoreTraceEnabled = self.getOptionValue("options.trace.cortexA9coreTrace.Cortex_A9_%d" % i)
            enableSource = coreTraceEnabled and thisCoreTraceEnabled
            self.setTraceSourceEnabled(self.PTMs[i], enableSource)
            self.setTriggerGeneratesDBGRQ(self.PTMs[i], self.getOptionValue("options.trace.cortexA9coreTrace.triggerhalt"))
            self.setContextIDEnabled(self.PTMs[i],
                                     self.getOptionValue("options.trace.cortexA9coreTrace.contextIDs"),
                                     self.getOptionValue("options.trace.cortexA9coreTrace.contextIDs.contextIDsSize"))

        ptmStartIndex = 0
        ptmEndIndex = 0

        ptmEndIndex += NUM_CORES_CORTEX_A9
        self.setInternalTraceRange(self.traceRangeOptions[CORTEX_A9_TRACE_OPTIONS], TraceRangeOptions("cortexA9coreTrace", self), self.PTMs[ptmStartIndex:ptmEndIndex])
        ptmStartIndex += NUM_CORES_CORTEX_A9

    def getManagedDevices(self, traceKey):
        '''Get the required set of managed devices for this configuration'''
        return self.mgdPlatformDevs | self.mgdTraceDevs.get(traceKey, set())

    def setTraceCaptureMethod(self, method):
        if method == "none":
            self.setETBTraceEnabled(False)
            self.setDSTREAMTraceEnabled(False)
        elif method == "ETB":
            self.setETBTraceEnabled(True)
            self.setDSTREAMTraceEnabled(False)
        elif method == "DSTREAM":
            self.setETBTraceEnabled(False)
            self.setDSTREAMTraceEnabled(True)

    def getPTMs(self):
        '''Get the PTMs'''
        return self.PTMs

    def setITMEnabled(self, enabled):
        '''Enable/disable the ITM trace source'''
        self.setTraceSourceEnabled(self.ITM, enabled)

    def setCoreTraceEnabled(self, enabled):
        '''Enable/disable the core trace sources'''
        for t in self.PTMs:
            self.setTraceSourceEnabled(t, enabled)


    # +------------------------------+
    # | Target independent functions |
    # +------------------------------+

    def addManagedPlatformDevices(self, devs):
        '''Add devices to the set of devices managed by the configuration'''
        for d in devs:
            self.mgdPlatformDevs.add(d)

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
        # port = 5 to trace core 0
        # port = 6 to trace core 1
        port = core + 5
        return port

    def createITM(self, itmDev, streamID, name):
        itm = ITMTraceSource(self, itmDev, streamID, name)
        # disabled by default - will enable with option
        itm.setEnabled(False)
        return itm

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


class DtslScript_RVI(DtslScript):
    @staticmethod
    def getOptionList():
        return [
            DTSLv1.tabSet("options", "Options", childOptions=[
                DTSLv1.tabPage("trace", "Trace", childOptions=[
                    DTSLv1.enumOption('traceCapture', 'Trace capture method', defaultValue="none",
                        values = [("none", "None"), ("ETB", "On Chip Trace Buffer (ETB)")],
                        setter=DtslScript.setTraceCaptureMethod),
                    DTSLv1.booleanOption('cortexA9coreTrace', 'Enable Cortex-A9 core trace', defaultValue=False,
                        childOptions =
                            # Allow each source to be enabled/disabled individually
                            [ DTSLv1.booleanOption('Cortex_A9_%d' % c, "Enable Cortex-A9 %d trace" % c, defaultValue=True)
                            for c in range(0, NUM_CORES_CORTEX_A9) ] +
                            [ DTSLv1.booleanOption('triggerhalt', "PTM Triggers halt execution", description="Enable the PTM triggers to halt execution", defaultValue=False) ] +
                            [ DTSLv1.booleanOption('contextIDs', "Enable PTM Context IDs", description="Controls the output of context ID values into the PTM output streams", defaultValue=True,
                                childOptions = [
                                    DTSLv1.enumOption('contextIDsSize', 'Context ID Size', defaultValue="32",
                                        values = [("8", "8 bit"), ("16", "16 bit"), ("32", "32 bit")])
                                    ])
                            ] +
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
                    DTSLv1.booleanOption('itm', 'Enable ITM trace', defaultValue=False,
                        setter=DtslScript.setITMEnabled),
                ])
            ])
        ]