import warnings as w
# This warning is caused by numpy updates and should
# be ignored for now.
w.simplefilter(action = 'ignore', category = FutureWarning)
import numpy as np
import nibabel as nib
import sys
import os
import glob
import shutil
import yaml
import time
np.set_printoptions(threshold=np.nan)
from lib.npMatrix3d import *
from lib.npMatrix2d import *
from lib.fileio import *
import src.blmm_inference as blmm_inference
import src.blmm_estimate as blmm_estimate

# ====================================================================================
#
# This file is the third stage of the BLMM pipeline. This stage reads in the product
# matrices output by each of the `blmm_batch` jobs during the second stage and s 
# them to obtain the product matrices for the overall model. It also calculates n_sv
# for the whole model and the overall mask.
#
# Following this, the `blmm_concat` code then seperates the voxels in the brain into
# two categories; "inner" and "ring" (explained in the developer notes below). Once
# this has been done the product matrices corresponding to "inner" and "ring" voxels
# are passed to `blmm_estimate`, which estimates the parameters of the model; beta,
# sigma2 and D. Following this, the product matrices and parameter estimates are 
# passed to `blmm_inference`, which generates statistic maps and other miscelanoues 
# output.
#
# ------------------------------------------------------------------------------------
#
# Author: Tom Maullin (Last edited: 04/04/2020)
#
# ------------------------------------------------------------------------------------
#
# The code takes the following inputs:
#
#  - ipath (optional): If specified, the first argument will be ased to be a
#                           path to an `inputs` yml file, following the same 
#                           formatting guidelines as `blmm_config.yml`. If not 
#                           specified, the default file `blmm_config.yml` will be 
#                           ased to contain the inputs.
#
# MARKER TODO
#
# ------------------------------------------------------------------------------------
# Developer notes:
# ------------------------------------------------------------------------------------
# In the following code I have used the following subscripts to indicate:
#
# _r - This means this is an array of values corresponding to voxels which
#      are present in between k and n-1 studies (inclusive), where k is
#      decided by the user specified thresholds. These voxels will typically
#      be on the edge of the brain and look like a "ring" around the brain,
#      hence "_r" for ring.
# 
# _i - This means that this is an array of values corresponding to voxels 
#      which are present in all n studies. These will usually look like
#      a smaller mask place inside the whole study mask. Hence "_i" for 
#      inner.
#
# _sv - This means this variable is spatially varying (There is a reading
#       per voxel). 
#
# ====================================================================================
def main(ipath, vb):

    # --------------------------------------------------------------------------------
    # Check inputs
    # --------------------------------------------------------------------------------
    # Inputs file is first argument
    with open(os.path.join(ipath), 'r') as stream:
        inputs = yaml.load(stream,Loader=yaml.FullLoader)

    # Voxel batch
    vb = int(vb)

    # Check if the maximum memory is saved.    
    if 'MAXMEM' in inputs:
        MAXMEM = eval(inputs['MAXMEM'])
    else:
        MAXMEM = 2**32

    # --------------------------------------------------------------------------------
    # Read basic inputs
    # --------------------------------------------------------------------------------
    OutDir = inputs['outdir']

    # Random factor variables.
    rfxmats = inputs['Z']

    # Number of random effects
    r = len(rfxmats)

    # Number of random effects for each factor, q
    nraneffs = []

    # Number of levels for each factor, l
    nlevels = []

    for k in range(r):

        rfxdes = loadFile(rfxmats[k]['f' + str(k+1)]['design'])
        rfxfac = loadFile(rfxmats[k]['f' + str(k+1)]['factor'])

        nraneffs = nraneffs + [rfxdes.shape[1]]
        nlevels = nlevels + [len(np.unique(rfxfac))]

    # Get number of random effects
    nraneffs = np.array(nraneffs)
    nlevels = np.array(nlevels)
    q = np.sum(nraneffs*nlevels)

    # Get number of unique random effects
    q_u = np.sum(nraneffs*(nraneffs+1)//2)
    
    # Get number of parameters
    L1 = str2vec(inputs['contrasts'][0]['c' + str(1)]['vector'])
    L1 = np.array(L1)
    p = L1.shape[0]
    del L1
    
    # Read in the nifti size and work out number of voxels.
    with open(inputs['Y_files']) as a:
        nifti_path = a.readline().replace('\n', '')
        nifti = loadFile(nifti_path)

    NIFTIsize = nifti.shape
    v = int(np.prod(NIFTIsize))

    # --------------------------------------------------------------------------------
    # Get n (number of observations) and n_sv (spatially varying number of
    # observations)
    # --------------------------------------------------------------------------------

    # Work out number of batchs
    with open(os.path.join(OutDir,'nb.txt')) as f:
        n_b = int(f.readline())

    print('nb: ', n_b)
        
    # load n_sv
    n_sv = loadFile(os.path.join(OutDir,'blmm_vox_n.nii')).get_data().reshape([v,1])

    # Get ns.
    X = loadFile(inputs['X'])
    n = X.shape[0]

    # --------------------------------------------------------------------------------
    # Read Mask 
    # --------------------------------------------------------------------------------
        
    # Read in the mask nifti.
    Mask = loadFile(os.path.join(OutDir,'blmm_vox_mask.nii')).get_data().reshape([v,1])

    if 'analysis_mask' in inputs:

        amask_path = inputs["analysis_mask"]
        
        # Read in the mask nifti.
        amask = loadFile(amask_path).get_data().reshape([v,1])

    else:

        # By default make amask ones
        amask = np.ones([v,1])

    # Get indices for whole analysis mask. These indices are the indices we
    # have recorded for the product matrices with respect to the entire volume
    amInds = get_amInds(amask)

    # ------------------------------------------------------------------------
    # Work out block of voxels we are looking at
    # ------------------------------------------------------------------------
    # Get indices for block. These indices have to be the indices we want to
    # compute, in relation to the entire volume. If we aren't partitioning by 
    # block these will be equal to amInds
    pnvb = pracNumVoxelBlocks(inputs)
    bamInds = get_amInds(amask, vb-1, pnvb) # Remem vb 0 indexed in py but 1 indexed in bash

    # ------------------------------------------------------------------------
    # Split the voxels into computable groups
    # ------------------------------------------------------------------------

    # Work out the number of voxels we can actually compute at a time.
    nvb = MAXMEM/(10*8*(q**2))

    # Work out number of groups we have to split iindices into.
    nvg = int(len(bamInds)//nvb+1)

    # Split voxels we want to look at into groups we can compute
    voxelGroups = np.array_split(bamInds, nvg)

    # Loop through list of voxel indices, looking at each group of voxels, in
    # turn.
    for cv in range(nvg):

        # Current group of voxels
        bamInds_cv = voxelGroups[cv]

        # Mask for current voxels
        Mask_cv = np.array(Mask)
        Mask_cv[~np.in1d(np.arange(v).reshape(v,1), bamInds_cv)]=0

        # Get indices of voxels in ring around brain where there are
        # missing studies.
        R_inds = np.sort(np.where((Mask_cv==1)*(n_sv<n))[0])

        # Work out the 'ring' indices, in relation to the analysis mask
        ix_r = np.argsort(np.argsort(R_inds))
        R_inds_am = np.sort(np.where(np.in1d(amInds,R_inds))[0])[ix_r]

        # Get indices of the "inner" volume where all studies had information
        # present. I.e. the voxels (usually near the middle of the brain) where
        # every voxel has a reading for every study.
        I_inds = np.sort(np.where((Mask_cv==1)*(n_sv==n))[0])

        # Work out the 'inner' indices, in relation to the analysis mask
        ix_i = np.argsort(np.argsort(I_inds))
        I_inds_am = np.sort(np.where(np.in1d(amInds,I_inds))[0])[ix_i]

        # ------------------------------------------------------------------------
        # Number of voxels in ring and inner
        # ------------------------------------------------------------------------

        # Number of voxels in ring
        v_r = R_inds.shape[0]

        # Number of voxels in inner mask
        v_i = I_inds.shape[0]

        # Number of voxels in whole (inner + ring) mask
        v_m = v_i + v_r

        # --------------------------------------------------------------------------------
        # Load X'X, X'Y, Y'Y, X'Z, Y'Z, Z'Z
        # --------------------------------------------------------------------------------

        # Ring X'Y, Y'Y, Z'Y
        XtY_r = readLinesFromNPY(os.path.join(OutDir,"tmp",'XtY.npy'), R_inds_am).reshape([v_r, p, 1])
        YtY_r = readLinesFromNPY(os.path.join(OutDir,"tmp",'YtY.npy'), R_inds_am).reshape([v_r, 1, 1])
        ZtY_r = readLinesFromNPY(os.path.join(OutDir,"tmp",'ZtY.npy'), R_inds_am).reshape([v_r, q, 1])

        # Inner X'Y, Y'Y, Z'Y
        XtY_i = readLinesFromNPY(os.path.join(OutDir,"tmp",'XtY.npy'), I_inds_am).reshape([v_i, p, 1])
        YtY_i = readLinesFromNPY(os.path.join(OutDir,"tmp",'YtY.npy'), I_inds_am).reshape([v_i, 1, 1])
        ZtY_i = readLinesFromNPY(os.path.join(OutDir,"tmp",'ZtY.npy'), I_inds_am).reshape([v_i, q, 1])

        # Ring Z'Z. Z'X, X'X
        if v_r:

            ZtZ_r = readUniqueAtB('ZtZ',OutDir,R_inds,n_b,True).reshape([v_r, q, q])
            ZtX_r = readUniqueAtB('ZtX',OutDir,R_inds,n_b,True).reshape([v_r, q, p])
            XtX_r = readUniqueAtB('XtX',OutDir,R_inds,n_b,True).reshape([v_r, p, p])
        
        if v_i:
                
            # Inner Z'Z. Z'X, X'X
            ZtZ_i = readUniqueAtB('ZtZ',OutDir,I_inds,n_b,False).reshape([1, q, q])
            ZtX_i = readUniqueAtB('ZtX',OutDir,I_inds,n_b,False).reshape([1, q, p])
            XtX_i = readUniqueAtB('XtX',OutDir,I_inds,n_b,False).reshape([1, p, p])    

        # --------------------------------------------------------------------------------
        # Calculate betahat = (X'X)^(-1)X'Y and output beta maps
        # --------------------------------------------------------------------------------

        REML = False

        # If we have indices where only some studies are present, work out X'X and
        # X'Y for these studies. (Remember X'Y, Y'Y and Z'Y have already had the 
        # analysis mask applied to them during the batch stage)
        if v_r:

            # Spatially varying nv for ring
            n_sv_r = n_sv[R_inds,:]

            # Transposed matrices
            YtX_r = XtY_r.transpose(0,2,1)
            YtZ_r = ZtY_r.transpose(0,2,1) 
            XtZ_r = ZtX_r.transpose(0,2,1)

            # Run parameter estimation
            beta_r, sigma2_r, D_r = blmm_estimate.main(inputs, R_inds, XtX_r, XtY_r, XtZ_r, YtX_r, YtY_r, YtZ_r, ZtX_r, ZtY_r, ZtZ_r, n_sv_r, nlevels, nraneffs)

            print('sigma2 (r mode) shape: ', sigma2_r.shape)

            # Run inference
            blmm_inference.main(inputs, nraneffs, nlevels, R_inds, beta_r, D_r, sigma2_r, n_sv_r, XtX_r, XtY_r, XtZ_r, YtX_r, YtY_r, YtZ_r, ZtX_r, ZtY_r, ZtZ_r)       
            
        if v_i:

            # Transposed matrices
            YtX_i = XtY_i.transpose(0,2,1)
            YtZ_i = ZtY_i.transpose(0,2,1) 
            XtZ_i = ZtX_i.transpose(0,2,1)

            # Run parameter estimation
            beta_i, sigma2_i, D_i = blmm_estimate.main(inputs, I_inds,  XtX_i, XtY_i, XtZ_i, YtX_i, YtY_i, YtZ_i, ZtX_i, ZtY_i, ZtZ_i, n, nlevels, nraneffs)

            # Run inference
            blmm_inference.main(inputs, nraneffs, nlevels, I_inds, beta_i, D_i, sigma2_i, n, XtX_i, XtY_i, XtZ_i, YtX_i, YtY_i, YtZ_i, ZtX_i, ZtY_i, ZtZ_i)

    w.resetwarnings()

# MARKER
def readUniqueAtB(AtBstr, OutDir, vinds, n_b, sv):

    # Work out the uniqueness mask for the spatially varying designs
    uniquenessMask = loadFile(os.path.join(OutDir,"tmp", 
        "blmm_vox_uniqueM.nii")).get_data()

    v = np.prod(uniquenessMask.shape)
    vcurrent = np.prod(vinds.shape)

    uniquenessMask=uniquenessMask.reshape(v)

    # Work out how many unique matrices there were
    maxM = np.int32(np.amax(uniquenessMask))

    if sv:
        # Work out the uniqueness mask inside the ring around the brain
        uniquenessMask = uniquenessMask[vinds]
    else:
        # Work out the uniqueness mask value inside the inner part of the brain
        uniquenessMask = uniquenessMask[vinds[0]] 


    # read in XtX
    AtB_batch_unique = np.load(
        os.path.join(OutDir,"tmp",AtBstr+".npy"))

    # Make zeros for outer ring of brain ZtZ, XtX, ZtX etc (remember A'B is still flattened)
    if sv:
        AtB = np.zeros((vcurrent, AtB_batch_unique.shape[1]))

    # Fill with unique maskings
    for m in range(1,maxM+1):

        if sv:
            # Work out Z'Z, Z'X and X'X for the ring
            AtB[np.where(uniquenessMask==m),:] = AtB_batch_unique[(m-1),:]

        # Work out Z'Z, Z'X and X'X for the inner
        else:
            if uniquenessMask == m:
                AtB = AtB_batch_unique[(m-1),:]

    return(AtB)


if __name__ == "__main__":
    main()