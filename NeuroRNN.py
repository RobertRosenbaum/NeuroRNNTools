
import torch
import torch.nn as nn
import numpy as np


# There are two ways of initializing recurrent weight matrices:
# 1)  Pass in the recurrent dimensions:
#         model = RateModel(N_recurrent=N, ...)
#     In this case, do not pass in recurrent_weight.
#     The spectral radius of the recurrent weight matrix can optionally be passed as rho_recurrent
#
# 2)  Pass in the weight matrix itself:
#         model = RateModel(recurrent_weight=J, ...)
#     In this case, do not pass in N_recurrent. It will be inferred from the matrix.
#     rho_recurrent will be ignored in this case.
#
# The input and output weight matrices are defined similarly, except there is an option to have
# no input and/or no output layer by not passing anything in (keep N and weight as None)
# In this case, the input and/or output layer will be an identity function.
#
# Example:
#     model = RateModel(recurrent_weight=J, N_input=100, rho_input=0.5)
# creates a model with recurrent weight matrix J, a new random matrix for the
# 100-dimensional input, and no readout matrix (so the output is the hidden state)
#
# TO DO:
#    - Implement network types (or just layers) EchoR and EchoZ which feed output back into input. Requires Readout==True
#    - Implement multiple RNN layers (maybe in separate function?)
#    - replace keep_old_state = False with initial_state = 'old' OR initial_state = None keeps old state
#      and initial_state = 0 zeros it out.
#    - Check to make sure initial state is consistent with z and r.
#    - Check that forward Euler actually makes sense wrt init condition. Maybe range(1,Nt) with init before that?
class RateModel(nn.Module):

    def __init__(self, recurrent, readin=None, readout=None, f='tanh', eta=1, rho_recurrent=1, rho_input=1, rho_output=1, bias_recurrent=False, bias_output=False, Network_Type='R'):
        super(RateModel, self).__init__()

        # Step size for RNN dynamics
        self.eta = eta

        self.Network_Type = Network_Type
        if Network_Type not in ('R','Z'):
            raise Exception("Network_Type must be 'R' or 'Z'.")

        # Bias True or False for each linear component.
        # Bias in input and recurrent would be redundant, so bias=False for input.
        self.bias_recurrent = bias_recurrent
        self.bias_output = bias_output

        # If recurrent is an int, then generate a matrix with that size.
        # If it's a matrix, use that matrix for the weights.
        if isinstance(recurrent, int):
            self.N_recurrent = recurrent
            self.rho_recurrent = rho_recurrent
            self.recurrent_layer = nn.Linear(self.N_recurrent, self.N_recurrent, bias=bias_recurrent)
            self.recurrent_layer.weight.data = rho_recurrent * torch.randn(self.N_recurrent, self.N_recurrent) / torch.sqrt(torch.tensor(self.N_recurrent))
        elif torch.is_tensor(recurrent) and len(recurrent.shape)==2:
            self.N_recurrent = recurrent.shape[0]
            self.rho_recurrent = None
            self.recurrent_layer = nn.Linear(self.N_recurrent, self.N_recurrent, bias=bias_recurrent)
            self.recurrent_layer.weight = nn.Parameter(recurrent)
        else:
            raise Exception('argument recurrent should be an int or a square, 2-dimensional tensor.')

        # Do the same for readin, except also allow readin==None, which means that there is no
        # readin layer, e.g., the readin layer is an identity function.
        if isinstance(readin, int):
            self.N_input = readin
            self.rho_input = rho_input
            self.input_layer = nn.Linear(self.N_input, self.N_recurrent, bias=False)
            self.input_layer.weight.data = rho_input * torch.randn(self.N_recurrent, self.N_input) / torch.sqrt(torch.tensor(self.N_input))
            self.Readin = True
        elif torch.is_tensor(readin) and len(readin.shape)==2:
            self.N_input = readin.shape[1]
            self.rho_input = None
            self.input_layer = nn.Linear(self.N_input, self.N_recurrent, bias=False)
            self.input_layer.weight.data = readin
            self.Readin = True
        elif (readin is None) or (readin is False):
            self.Readin = False
            self.N_input = None
            self.rho_input = None
            self.input_layer = nn.Identity()
        else:
            raise Exception('readin should be an int, a 2-dim tensor, False, or None')

        # Same as readin above.
        if isinstance(readout, int):
            self.N_output = readout
            self.rho_output = rho_output
            self.output_layer = nn.Linear(self.N_recurrent, self.N_output, bias=bias_output)
            self.output_layer.weight.data = rho_output * torch.randn(self.N_output, self.N_recurrent) / torch.sqrt(torch.tensor(self.N_output))
            self.Readout = True
        elif torch.is_tensor(readout) and len(readout.shape)==2:
            self.N_output = readout.shape[0]
            self.rho_output = None
            self.output_layer = nn.Linear(self.N_recurrent, self.N_output, bias=bias_output)
            self.output_layer.weight.data = readout
            self.Readout = True
        elif (readout is None) or (readout is False):
            self.Readout = False
            self.N_output = None
            self.rho_output = None
            self.output_layer = nn.Identity()
        else:
            raise Exception('readout should be an int, a 2-dim tensor, False, or None')

        # # Same as readin above.
        # if (echo is True):
        #     self.rho_echo = rho_echo
        #     self.echo_layer = nn.Linear(self.N_output, self.N_recurrent, bias=bias_echo)
        #     self.echo_layer.weight.data = rho_echo * torch.randn(self.N_recurrent, self.N_output) / torch.sqrt(torch.tensor(self.N_output))
        #     self.echo = True
        # elif torch.is_tensor(echo) and len(echo.shape)==2:
        #     self.rho_echo = None
        #     self.echo_layer = nn.Linear(self.N_output, self.N_recurrent, bias=bias_echo)
        #     self.echo_layer.weight.data = echo
        #     self.echo = True
        # elif (echo is None) or (echo is False):
        #     self.echo = False
        #     self.rho_echo = None
        #     self.echo_layer = (lambda x: 0)
        # else:
        #     raise Exception('echo should be True, a 2-dim tensor, False, or None')


        # activation == fI curve, f, can be a string for relu, tanh, or identity
        # OR it can be any function
        if f == 'relu':
            self.f = torch.relu
        elif f == 'tanh':
            self.f = torch.tanh
        elif f == 'id':
            self.f = (lambda x: x)
        elif callable(f):
            self.f = f
        else:
            raise Exception("f should be 'tanh', 'relu', 'id', or a callable function.")

        # Initialize recurrent state
        self.hidden_state = None


    # Forward pass.
    # If Nt==None then the second dimension of x is assumed to be time.
    # If Nt is an integer, then x is interpreted to be constant in time and Nt is the number of time steps.
    def forward(self, x, Nt = None, return_time_series = True, initial_state = 'zero'):

        # Get batch size, device, and requires_grad
        batch_size = x.shape[0]
        this_device = x.device
        this_req_grad = self.recurrent_layer.weight.requires_grad

        # Check that last dim of input is correct
        if self.Readin:
            if x.shape[-1]!=self.N_input:
                raise Exception('last dim of x should be N_input ='+str(self.N_input)+'but got'+str(x.shape[-1]))
        else:
            if x.shape[-1]!=self.N_recurrent:
                raise Exception('last dim of x should be N_recurrent ='+str(self.N_recurrent)+'but got'+str(x.shape[-1]))

        # If x is 3-dimensional then Nt should be None (or equal to second dim of x) and input is dynamical.
        # Otherwise, x should be 2-dimensional and Nt needs to be passed in as an int, and input is time-constant.
        if len(x.shape)==3 and ((Nt is None) or Nt==x.shape[1]) :
            Nt = x.shape[1]
            dynamical_input = True
        elif (Nt is not None) and len(x.shape)==2:
            dynamical_input = False
        else:
            raise Exception('x should be 3 dim (in which case Nt should be None) or x should be 2 dim in which case you need to pass Nt.')

        # If initial_state is 'zero' initialize to zeros
        # If initial_state is 'keep' then keep old initial state
        # If initial_state is a tensor, intialize to that state
        if initial_state == 'zero':
            self.hidden_state = torch.zeros(batch_size, self.N_recurrent, requires_grad=this_req_grad).to(this_device)
        elif initial_state == 'keep':
            if (not torch.is_tensor(self.hidden_state)) or (not (self.hidden_state.shape[0]==batch_size)):
                print("initial_state = 'keep' but old state is not consistent type or shape. Using zero initial state instead.")
                self.hidden_state = torch.zeros(batch_size, self.N_recurrent, requires_grad=this_req_grad).to(this_device)
        elif torch.is_tensor(initial_state):
            self.hidden_state = initial_state
        else:
            raise Exception("initial_state should be 'zero', 'keep', or an initial state tensor.")
        self.hidden_state.to(this_device)

        # If we return time series, then initialize a variable for it.
        if return_time_series:
            hidden_state_history = torch.zeros(batch_size, Nt, self.N_recurrent).to(this_device)
        else:
            hidden_state_history = None

        # Rate type network
        if self.Network_Type == 'R':
            if dynamical_input:
                for i in range(Nt):
                    self.hidden_state = self.hidden_state + self.eta * (-self.hidden_state + self.f(self.recurrent_layer(self.hidden_state) + self.input_layer(x[:, i, :])))
                    if return_time_series:
                        hidden_state_history[:, i, :] = self.hidden_state
            else:
                JxX = self.input_layer(x)
                for i in range(Nt):
                    self.hidden_state = self.hidden_state + self.eta * (-self.hidden_state + self.f(self.recurrent_layer(self.hidden_state) + JxX))
                    if return_time_series:
                        hidden_state_history[:, i, :] = self.hidden_state
            if return_time_series:
                return self.output_layer(hidden_state_history)
            else:
                return self.output_layer(self.hidden_state)

        # Z type network
        elif self.Network_Type == 'Z':
            if dynamical_input:
                for i in range(Nt):
                    self.hidden_state = self.hidden_state + self.eta * (-self.hidden_state + self.recurrent_layer(self.f(self.hidden_state)) + self.input_layer(x[:, i, :]))
                    if return_time_series:
                        hidden_state_history[:, i, :] = self.hidden_state
            else:
                JxX = self.input_layer(x)
                for i in range(Nt):
                    self.hidden_state = self.hidden_state + self.eta * (-self.hidden_state + self.recurrent_layer(self.f(self.hidden_state)) + JxX)
                    if return_time_series:
                        hidden_state_history[:, i, :] = self.hidden_state
            if return_time_series:
                return self.output_layer(self.f(hidden_state_history))
            else:
                return self.output_layer(self.f(self.hidden_state))

        else:
            raise Exception("Network_Type must be 'R' or 'Z'.")



# Spiking neural net model
class SpikingModel(nn.Module):

    def __init__(self, recurrent, tausyn, readin=None, NeuronModel='EIF', NeuronParams={}):
        super(SpikingModel, self).__init__()

        if torch.is_tensor(recurrent) and len(recurrent.shape) == 2:
            self.N_recurrent = recurrent.shape[0]
            self.recurrent_layer = nn.Linear(self.N_recurrent, self.N_recurrent, bias=False)
            self.recurrent_layer.weight = nn.Parameter(recurrent)
        else:
            raise Exception('recurrent should be an NxN tensor')

        if torch.is_tensor(readin) and len(readin.shape)==2:
            self.Readin = True
            self.N_input = readin.shape[1]
            self.input_layer = nn.Linear(self.N_input, self.N_recurrent, bias=False)
            self.input_layer.weight.data = readin
        elif (readin is None) or (readin is False):
            self.Readin = False
            self.N_input = None
            self.input_layer = nn.Identity()
        else:
            raise Exception('readin should be a 2-dim tensor, False, or None')

        # Synaptic time constants
        self.tausyn = tausyn

        # Neuron parameters
        if NeuronModel == 'EIF':
            # Get each param from NeuronParams or use default if key isn't in NeuronParams
            self.taum = NeuronParams.get('taum',10)
            self.EL = NeuronParams.get('EL',-72)
            self.Vth = NeuronParams.get('Vth',0)
            self.Vre = NeuronParams.get('Vre',-72)
            self.VT = NeuronParams.get('VT',-55)
            self.DT = NeuronParams.get('DT',1)
            self.f = (lambda V,I: ((-(V-self.EL)+self.DT*torch.exp((V-self.VT)/self.DT)+I)/self.taum))
        elif NeuronModel == 'LIF':
            self.taum = NeuronParams.get('taum',10)
            self.EL = NeuronParams.get('EL',-72)
            self.Vth = NeuronParams.get('Vth',-55)
            self.Vre = NeuronParams.get('Vre',-72)
            self.f = (lambda V,I: ((-(V - self.EL)+I)/self.taum))
        elif callable(NeuronModel):
            self.Vth = NeuronModel['Vth']
            self.Vre = NeuronModel['Vre']
            self.f = NeuronModel
        else:
            raise Exception("NeuronModel should be 'EIF', 'LIF', or a function of two variables (V,I).")

        # Initialize state, which contains V and zsyn
        self.V = None
        self.Y = None

    # Forward pass.
    # If Nt==None then the second dimension of x is assumed to be time.
    # If Nt is an integer, then x is interpreted to be constant in time and Nt is the number of time steps.
    def forward(self, x0, dt, x=None, T=None, initial_V='random', initial_Y='zero', dtRecord = None, Tburn = 0, VIRecord = []):

        # Get batch size, device, and requires_grad
        batch_size = x0.shape[0]
        this_device = x0.device
        this_req_grad = self.recurrent_layer.weight.requires_grad

        # Make sure x0 is correct shape
        if len(x0.shape)!=2 or x0.shape[1]!=self.N_recurrent:
            raise Exception('x0 should be (batch_size)x(N_recurent).')

        # Check shape and type of x. Set Nt, T values accordingly
        if torch.is_tensor(x) and len(x.shape)==3:
            dynamical_input = True
            Nt = x.shape[1]
            T = Nt*dt
            if x.shape[0]!=x0.shape[0]:
                raise Exception('First dim of x and x0 should be the same (batch_size).')
            if self.Readin:
                if x.shape[2]!=self.N_input:
                    raise Exception('When x is 3-dim and Readin is True, last dim of x should be N_input.')
            else:
                if x.shape[2]!=self.N_recurrent:
                    raise Exception('When x is 3-dim and Readin is False, last dim of x should be N_recurrent.')
        elif torch.is_tensor(x) and len(x.shape)==2:
            dynamical_input = False
            if T is None:
                raise Exception('If x is not dynamical (2-dim) then T cannot be None.')
            Nt = int(T/dt)
            if x.shape[0]!=x0.shape[0]:
                raise Exception('First dim of x and x0 should be the same (batch_size).')
            if self.Readin:
                if x.shape[1]!=self.N_input:
                    raise Exception('When Readin is True, last dim of x should be N_input.')
            else:
                if x.shape[1]!=self.N_recurrent:
                    raise Exception('When Readin is False, last dim of x should be N_recurrent.')
        elif x is None:
            dynamical_input = False
            if T is None:
                raise Exception('If x is None then T cannot be None.')
            else:
                Nt = int(T/dt)
            # if self.Readin:
            #     x = torch.zeros(batch_size,self.N_input)
            # else:
            #     x = torch.zeros(batch_size,self.N_recurrent)
        else:
            raise Exception('x should be a 3-dim tensor, 2-dim tensor, or None.')

        # If initial_V is 'zero' initialize to Vre (not actually zero)
        # If initial_V is 'rand' initialize to uniform dist from Vre to Vth
        # If initial_state is 'keep' then keep old initial state
        # If initial_state is a tensor, intialize to that state
        if initial_V == 'zero':
            self.V = torch.zeros(batch_size, self.N_recurrent, requires_grad=this_req_grad).to(this_device)+self.Vre
        elif initial_V == 'random':
            self.V = (self.Vth-self.Vre)*torch.rand(batch_size, self.N_recurrent, requires_grad=this_req_grad).to(this_device)+self.Vre
        elif initial_V == 'keep':
            if (not torch.is_tensor(self.V)) or (self.V.shape[0] != batch_size):
                print("initial_V was 'keep' but V was wrong type or shape. Using random init instead")
                self.V = (self.Vth-self.Vre)*torch.rand(batch_size, self.N_recurrent, requires_grad=this_req_grad).to(this_device)+self.Vre
        elif torch.is_tensor(initial_V) and initial_V.shape==(batch_size,self.N_recurrent):
            self.V = initial_V
        else:
            raise Exception("initial_V should be 'zero', 'keep', or an initial tensor of shape (batch_size,N_recurrent)="+str((batch_size,self.N_recurrent)))

        # Same as initial_V except no random option
        if initial_Y == 'zero':
            self.Y = torch.zeros(batch_size, self.N_recurrent).to(this_device)
        elif initial_Y == 'keep':
            if (not torch.is_tensor(self.Y)) or (self.Y.shape[0] != batch_size):
                print("initial_Y was 'keep' but Y was wrong type or shape. Using zero init instead")
                self.Y = torch.zeros(batch_size, self.N_recurrent).to(this_device)
        elif torch.is_tensor(initial_Y) and initial_Y.shape==(batch_size,self.N_recurrent):
            self.Y = initial_Y
        else:
            raise Exception("initial_Y should be 'zero', 'keep', or an initial tensor of shape (batch_size,N_recurrent)="+str((batch_size,self.N_recurrent)))
        self.Y.requires_grad = this_req_grad
        self.Y.to(this_device)

        # Initialize dictionary that will store results of sim
        SimResults = {}
        SimResults['r'] = torch.zeros(batch_size, self.N_recurrent, requires_grad=this_req_grad).to(this_device)
        if dtRecord is None:
            RecordSandY = False
            SimResults['S'] = None
            SimResults['Y'] = None
        else:
            RecordSandY = True
            NdtRecord = int(dtRecord/dt)
            if NdtRecord<=0 or NdtRecord>Nt:
                raise Exception('dtRecord should be between dt and T respectively.')
            NtRecord = int(T/dtRecord)
            SimResults['S'] = torch.zeros(batch_size, NtRecord, self.N_recurrent, requires_grad=this_req_grad).to(this_device)
            SimResults['Y'] = torch.zeros(batch_size, NtRecord, self.N_recurrent, requires_grad=this_req_grad).to(this_device)

        if isinstance(VIRecord,list) and len(VIRecord)>0:
            RecordV = True
            NVRecord = len(VIRecord)
            SimResults['V'] = torch.zeros(batch_size, Nt, NVRecord, requires_grad=this_req_grad).to(this_device)
        elif (VIRecord is None) or (VIRecord == []):
            RecordV = False
            SimResults['V'] = None

        # Now start the acutal forward pass
        S = torch.zeros(batch_size, self.N_recurrent, requires_grad=this_req_grad).to(this_device)

        #PROBLEM: Z IS NOT USED, DEF OF Z MAKES NO SENSE Z THINK. USE Y INSTEAD?
        if (x is not None) and (not dynamical_input):
            JxX = self.input_layer(x)
        for i in range(Nt):
            Z = self.recurrent_layer(self.Y)+x0
            if x is not None:
                if dynamical_input:
                    Z = Z + self.input_layer(x[:,i,:])
                else:
                    Z = Z + JxX
            self.V = self.V + dt*self.f(self.V, Z)
            S.zero_()
            indices = torch.nonzero(self.V>=self.Vth, as_tuple = True)
            S[indices] = 1.0
            self.V[indices] = self.Vre
            #S[self.V >= self.Vth]=1.0
            #self.V[self.V >= self.Vth] = self.Vre

            self.Y = self.Y + (1/self.tausyn)*(-dt*self.Y+S)

            if i*dt>=Tburn:
                SimResults['r'] += S

            if RecordV:
                SimResults['V'][:,i,:] = self.V[:,VIRecord]+S[:,VIRecord]*(self.Vth-self.V[:,VIRecord])
                #if len(indices)>0:
                #    print('!',S[:,VIRecord]*(self.Vth-self.V[:,VIRecord]))
                #SimResults['V'][indices[VIRecord],i]=self.Vth

            if RecordSandY:
                irecord = int(i*dt/dtRecord)
                SimResults['S'][:, irecord, :] += S
                SimResults['Y'][:, irecord, :] += self.Y

        SimResults['r'] *= (1/(T-Tburn))

        if RecordSandY:
            SimResults['S'] *= (1/NdtRecord)
            SimResults['Y'] *= (1 / NdtRecord)

        return SimResults
