import warnings as w
# This warning is caused by numpy updates and should
# be ignored for now.
w.simplefilter(action = 'ignore', category = FutureWarning)
import numpy as np
import os
np.set_printoptions(threshold=np.nan)
from scipy import stats
from lib.npMatrix3d import *
from lib.npMatrix2d import *
from lib.fileio import *
from lib.est3D import *


# ------------------------------------------------------------------------------------
#
# This file is the fourth stage of the BLMM pipeline. 
#
# ------------------------------------------------------------------------------------
#
# Author: Tom Maullin (Last edited: 04/04/2020)
#
# ------------------------------------------------------------------------------------
#
# The code takes the following inputs:
#
#  - `inputs`: The contents of the `inputs.yml` file, loaded using the `yaml` python 
#              package.
#  - `inds`: The (flattened) indices of the voxels we wish to perform parameter
#            estimation for.
#  - `XtX`: X transpose multiplied by X (can be spatially varying or non-spatially 
#           varying). 
#  - `XtY`: X transpose multiplied by Y (spatially varying.
#  - `XtZ`: X transpose multiplied by Z (can be spatially varying or non-spatially 
#           varying).
#  - `YtX`: Y transpose multiplied by X (spatially varying.
#  - `YtY`: Y transpose multiplied by Y (spatially varying.
#  - `YtZ`: Y transpose multiplied by Z (spatially varying.
#  - `ZtX`: Z transpose multiplied by X (can be spatially varying or non-spatially 
#           varying).
#  - `ZtY`: Z transpose multiplied by Y (spatially varying.
#  - `ZtZ`: Z transpose multiplied by Z (can be spatially varying or non-spatially 
#           varying).
#  - `n`: The number of observations (can be spatially varying or non-spatially 
#         varying). 
#
# ------------------------------------------------------------------------------------
def main(inputs, inds, XtX, XtY, ZtX, ZtY, ZtZ, XtZ, YtZ, YtY, YtX, n, nlevels, nparams):

    # ----------------------------------------------------------------------
    #  Read in one input nifti to get size, affines, etc.
    # ----------------------------------------------------------------------
    with open(inputs['Y_files']) as a:
        nifti_path = a.readline().replace('\n', '')
        nifti = loadFile(nifti_path)

    # Work out the dimensions of the NIFTI images
    NIFTIsize = nifti.shape


    # ----------------------------------------------------------------------
    # Input variables
    # ----------------------------------------------------------------------

    # Output directory
    OutDir = inputs['outdir']

    # Convergence tolerance
    if "tol" in inputs:
        tol=inputs['tol']
    else:
        tol=1e-6

    # Estimation method
    if "method" in inputs:
        method=inputs['method']
    else:
        method='pSFS'

    # ----------------------------------------------------------------------
    # Preliminary useful variables
    # ---------------------------------------------------------------------- 

    # Scalar quantities
    v = np.prod(inds.shape) # (Number of voxels we are looking at)
    p = XtX.shape[1] # (Number of Fixed Effects parameters)
    qu = np.sum(nparams*(nparams+1)//2) # (Number of unique random effects)


    # REML is just a backdoor option at the moment as it isn't that useful
    # in the large n setting. For now we just set it to false.
    REML = False

    # ----------------------------------------------------------------------
    # Parameter estimation
    # ----------------------------------------------------------------------  

    if method=='pSFS': # Recommended, default method
        paramVec = pSFS3D(XtX, XtY, ZtX, ZtY, ZtZ, XtZ, YtZ, YtY, YtX, nlevels, nparams, 1e-6, n, reml=REML)
    
    if method=='FS': 
        paramVec = FS3D(XtX, XtY, ZtX, ZtY, ZtZ, XtZ, YtZ, YtY, YtX, nlevels, nparams, 1e-6, n, reml=REML)

    if method=='SFS': 
        paramVec = SFS3D(XtX, XtY, ZtX, ZtY, ZtZ, XtZ, YtZ, YtY, YtX, nlevels, nparams, 1e-6, n, reml=REML)

    if method=='pFS': 
        paramVec = pFS3D(XtX, XtY, ZtX, ZtY, ZtZ, XtZ, YtZ, YtY, YtX, nlevels, nparams, 1e-6, n, reml=REML)

    # ----------------------------------------------------------------------
    # Parameter outputting
    # ----------------------------------------------------------------------    

    # Dimension of beta volume
    dimBeta = (NIFTIsize[0],NIFTIsize[1],NIFTIsize[2],p)

    # Dimension of D volume
    dimD = (NIFTIsize[0],NIFTIsize[1],NIFTIsize[2],qu)

    # Get the indices in the paramvector corresponding to D matrices
    IndsDk = np.int32(np.cumsum(nparams*(nparams+1)//2) + p + 1)
    IndsDk = np.insert(IndsDk,0,p+1)

    # Output beta estimate
    beta = paramVec[:, 0:p]
    addBlockToNifti(os.path.join(OutDir, 'blmm_vox_beta.nii'), beta, inds,volInd=None,dim=dimBeta,aff=nifti.affine,hdr=nifti.header)        
    
    # Output sigma2 estimate
    sigma2 = paramVec[:,p:(p+1),:]
    addBlockToNifti(os.path.join(OutDir, 'blmm_vox_sigma2.nii'), sigma2, inds,volInd=0,dim=NIFTIsize,aff=nifti.affine,hdr=nifti.header)

    # Output unique D elements (i.e. [vech(D_1),...vech(D_r)])
    vechD = paramVec[:,(p+1):,:].reshape((v,qu))
    addBlockToNifti(os.path.join(OutDir, 'blmm_vox_D.nii'), vechD, inds,volInd=None,dim=dimD,aff=nifti.affine,hdr=nifti.header) 

    # Reconstruct D
    Ddict = dict()
    # D as a dictionary
    for k in np.arange(len(nparams)):

        Ddict[k] = vech2mat3D(paramVec[:,IndsDk[k]:IndsDk[k+1],:])
      
    # Full version of D
    D = getDfromDict3D(Ddict, nparams, nlevels)

    return(beta, sigma2, D)